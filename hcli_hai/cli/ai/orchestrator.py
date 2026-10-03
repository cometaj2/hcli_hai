import logger
import threading
import traceback
import os
import re
import json
import openai
import config as a
from ai import ai
from ai import agentrunner as agr
from ai.policy import bash_error, empty_step, next_runnable
from pathlib import Path
from hcli_problem_details import ConflictError

log = logger.Logger()

MAX_TASKS = 8
MAX_REPLANS = 2
MAX_ADVANCE = 8

TASK_KEYS = ("id", "intent", "acceptance", "depends_on")


class Orchestrator:
    """Break a user goal into tasks, then hand the active task to AgentRunner.

    The server never runs bash. A continue step is stored for the client to execute
    via `hai agent plan` and post back through `hai agent next`.
    """

    _instance = None
    _init_lock = threading.RLock()

    def __new__(cls):
        with cls._init_lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance.initialized = False
            return cls._instance

    def __init__(self):
        if self.initialized:
            return
        with self._init_lock:
            if self.initialized:
                return
            self.rlock = threading.RLock()
            self.lock = threading.RLock()
            self.config = a.Config()
            self._is_vibing = False
            self.initialized = True
            self.ai = ai.AI()
            self.runner = agr.AgentRunner()
            self.behavior = self._load_behavior()

    def _load_behavior(self):
        path = self.config.dot_hai_orchestrator_file
        if os.path.exists(path):
            return Path(path).read_text(encoding="utf-8")
        return a.ORCHESTRATOR_MD

    def set_vibe(self, should_vibe):
        with self.rlock:
            self._is_vibing = should_vibe
            self.ai.contextmgr.plan.clear()
            if should_vibe is True:
                log.info("orchestrator started.")
            else:
                log.info("orchestrator stopped.")

    def is_vibing(self):
        with self.rlock:
            return self._is_vibing

    def pending_bash(self):
        with self.rlock:
            public = self.ai.contextmgr.plan.public()
            if public.get("status") != "continue":
                return None
            bash = (public.get("bash") or "").strip()
            return bash or None

    def status(self):
        with self.rlock:
            if not self._is_vibing:
                return "inactive"
            public = self.ai.contextmgr.plan.public()
            if public.get("status") == "continue" and public.get("bash"):
                return "next"
            plan_status = public.get("plan_status") or "idle"
            if plan_status == "planning":
                return "planning"
            if plan_status in ("blocked", "need_help"):
                return "blocked"
            if plan_status == "done":
                return "done"
            if plan_status == "idle" or not public.get("goal"):
                return "idle"
            return "planning"

    def task(self):
        if not self.is_vibing():
            return None
        return self.ai.contextmgr.get_step()

    def public(self):
        return self.ai.contextmgr.plan.dumps()

    def harness(self):
        with self.lock:
            if not self.is_vibing():
                return None
            if self.pending_bash():
                log.info("orchestrator already waiting for hai agent next.")
                return self.public()

            messages = self.ai.contextmgr.messages() or []
            user = ""
            if messages:
                user = messages[-1].get("content") or ""
            user = user.strip()
            if not user:
                return self._finish("need_help", "no user goal to plan")

            log.info("orchestrator planning.")
            self.ai.contextmgr.plan.clear()
            self._set_plan_status("planning", user)
            tasks, critique = self._plan_tasks(user, None)
            if not tasks:
                return self._finish("need_help", critique or "could not break the goal into tasks")

            tasks = self._ensure_catalog(tasks)
            self._install(user, tasks, replans=0)
            return self._run_until_blocked()

    def next(self, observation):
        with self.lock:
            if not self.is_vibing():
                log.warning("agent is not running; refusing next.")
                return None
            pending = self.pending_bash()
            if not pending:
                log.warning("no pending bash step to observe; refusing next.")
                return None

            log.info("orchestrator stored observation for: %s" % pending)
            self.ai.contextmgr.append_observation(pending, observation or "")
            return self.public()

    def mark(self):
        with self.lock:
            if not self.is_vibing():
                log.warning("agent is not running; refusing mark.")
                return None
            pending = self.pending_bash()
            if not pending:
                log.warning("no pending bash step to mark.")
                return self.public()
            if not self.ai.contextmgr.plan.has_observation(pending):
                msg = "hai agent next has not recorded an observation for this step"
                log.error(msg)
                raise ConflictError(detail=msg)
            log.info("orchestrator marking step done: %s" % pending)
            self._set_plan_status("planning")
            return self._run_until_blocked()

    def _plan_state(self):
        return self.ai.contextmgr.plan.doc()

    def _write(self, doc):
        self.ai.contextmgr.plan.replace(doc)

    def _set_plan_status(self, plan_status, goal=None):
        doc = self._plan_state()
        doc["plan_status"] = plan_status
        if goal is not None:
            doc["goal"] = goal
        doc["step"] = empty_step()
        self._write(doc)

    def _install(self, goal, tasks, replans):
        doc = self._plan_state()
        doc["goal"] = goal
        doc["plan_status"] = "active"
        doc["tasks"] = tasks
        doc["cursor"] = ""
        doc["step"] = empty_step()
        doc["say"] = ""
        doc["replans"] = replans
        self._write(doc)
        log.info("orchestrator installed %d task(s)." % len(tasks))

    def _run_until_blocked(self):
        for _ in range(MAX_ADVANCE):
            doc = self._plan_state()
            task = next_runnable(doc.get("tasks") or [])
            if task is None:
                return self._finish("done", self._summary(doc))

            task["status"] = "active"
            doc["cursor"] = task.get("id") or ""
            doc["plan_status"] = "planning"
            doc["step"] = empty_step()
            self._write(doc)
            log.info("orchestrator task %s active: %s" % (task.get("id"), task.get("intent")))

            step = self.runner.step(self._with_prior(doc, task), task.get("observations") or [])
            if step is None:
                step = {
                    "status": "need_help",
                    "goal": task.get("intent") or "",
                    "why": "",
                    "bash": "",
                    "say": "runner produced no step",
                }

            if step.get("status") == "continue":
                bash = (step.get("bash") or "").strip()
                critique = bash_error(bash)
                if not critique and self._already_observed(doc, bash):
                    critique = None
                    step = {
                        "status": "done",
                        "goal": task.get("intent") or "",
                        "why": "",
                        "bash": "",
                        "say": "already observed %s; not running it again" % bash,
                    }
                if critique:
                    step = {
                        "status": "need_help",
                        "goal": task.get("intent") or "",
                        "why": "",
                        "bash": "",
                        "say": critique,
                    }
                else:
                    doc_ids = self._plan_state()
                    seq = int(doc_ids.get("step_seq") or 0) + 1
                    step["id"] = "s%d" % seq
                    step["claimed"] = False
                    doc_ids["step_seq"] = seq
                    self._write(doc_ids)

            doc = self._plan_state()
            current = self._task(doc, task.get("id"))
            if current is None:
                return self._finish("need_help", "active task disappeared")

            if step.get("status") == "continue":
                current["status"] = "active"
                doc["cursor"] = current.get("id") or ""
                doc["plan_status"] = "active"
                doc["step"] = step
                doc["say"] = ""
                self._write(doc)
                log.info("orchestrator waiting for client to run: %s" % step.get("bash"))
                return self.public()

            if step.get("status") == "done":
                current["status"] = "done"
                current["result"] = step.get("say") or ""
                doc["cursor"] = current.get("id") or ""
                doc["step"] = empty_step()
                doc["plan_status"] = "active"
                self._write(doc)
                log.info("orchestrator task %s done." % current.get("id"))
                continue

            return self._on_need_help(step.get("say") or "task blocked")

        return self._finish("need_help", "advanced too many tasks without a client command")

    def _on_need_help(self, blocker):
        doc = self._plan_state()
        replans = int(doc.get("replans") or 0)
        goal = doc.get("goal") or ""
        if replans >= MAX_REPLANS:
            return self._finish("need_help", blocker)

        log.info("orchestrator replanning (%d/%d): %s" % (replans + 1, MAX_REPLANS, blocker))
        doc["plan_status"] = "planning"
        doc["replans"] = replans + 1
        self._write(doc)

        tasks, critique = self._plan_tasks(goal, blocker)
        if not tasks:
            return self._finish("need_help", critique or blocker)

        done = [t for t in (doc.get("tasks") or []) if t.get("status") == "done"]
        done_ids = {t.get("id") for t in done}
        fresh = []
        for task in tasks:
            if task.get("id") in done_ids:
                continue
            fresh.append(task)
        if not fresh:
            return self._finish("need_help", blocker)

        known = done_ids | {t.get("id") for t in fresh}
        for task in fresh:
            task["depends_on"] = [dep for dep in (task.get("depends_on") or []) if dep in known and dep != task.get("id")]
        self._install(goal, done + fresh, replans + 1)
        return self._run_until_blocked()

    def _already_observed(self, doc, bash):
        wanted = (bash or "").strip()
        if not wanted:
            return False
        for task in doc.get("tasks") or []:
            for item in task.get("observations") or []:
                if (item.get("bash") or "").strip() == wanted:
                    return True
        return False

    def _with_prior(self, doc, task):
        call = dict(task)
        notes = []
        for earlier in doc.get("tasks") or []:
            if earlier.get("id") == task.get("id"):
                continue
            learned = (earlier.get("result") or "").strip()
            obs = earlier.get("observations") or []
            snippet = ""
            if obs:
                snippet = (obs[-1].get("result") or "").strip()
                if len(snippet) > 500:
                    snippet = snippet[:500] + "\n... (truncated)"
            if learned or snippet:
                notes.append("%s: %s\n%s" % (earlier.get("id"), learned, snippet))
        if notes:
            hint = (call.get("hint") or "").strip()
            prior = "Earlier tasks already observed:\n" + "\n".join(notes)
            call["hint"] = (hint + "\n" + prior).strip()
        return call

    def _task(self, doc, task_id):
        for task in doc.get("tasks") or []:
            if task.get("id") == task_id:
                return task
        return None

    def _finish(self, status, say):
        doc = self._plan_state()
        doc["plan_status"] = status
        doc["say"] = say or ""
        doc["step"] = {
            "status": "done" if status == "done" else "need_help",
            "goal": doc.get("goal") or "",
            "why": "",
            "bash": "",
            "say": say or "",
        }
        doc["cursor"] = ""
        self._write(doc)
        self._join(doc["step"]["status"], say or "")
        log.info("orchestrator finished: %s" % status)
        return self.public()

    def _summary(self, doc):
        lines = []
        for task in doc.get("tasks") or []:
            if task.get("status") != "done":
                continue
            learned = (task.get("result") or "").strip()
            if learned:
                lines.append("%s: %s" % (task.get("intent") or task.get("id"), learned))
        if lines:
            return "\n".join(lines)
        return "The goal is done. No task reported a separate result."

    def _join(self, status, say):
        doc = self._plan_state()
        goal = (doc.get("goal") or "").strip()
        tasks = doc.get("tasks") or []

        if status == "done":
            header = "**Task completed.**"
        else:
            header = "**Agent needs help.**"

        parts = [header]
        if goal:
            parts.append("**Goal:** %s" % goal)

        trace = []
        n = 1
        for task in tasks:
            for item in task.get("observations") or []:
                cmd = (item.get("bash") or "").strip()
                result = (item.get("result") or "").strip()
                if len(result) > 800:
                    result = result[:800] + "\n... (truncated)"
                trace.append("%d. `%s`\n > %s" % (n, cmd, result))
                n += 1
        if trace:
            parts.append("**Execution trace:**\n" + "\n\n".join(trace))

        task_lines = []
        for task in tasks:
            task_lines.append("- %s [%s] %s" % (
                task.get("id") or "?",
                task.get("status") or "pending",
                task.get("intent") or "",
            ))
        if task_lines:
            parts.append("**Tasks:**\n" + "\n".join(task_lines))

        parts.append("**Result:**\n%s" % say)
        self.ai.commit_response("\n\n".join(parts))

    def _ensure_catalog(self, tasks):
        for task in tasks:
            intent = (task.get("intent") or "").lower()
            if "huckle cli ls" in intent or "list available hcli" in intent:
                task["hint"] = "First bash must be exactly: huckle cli ls"
                return tasks
        ids = {t.get("id") for t in tasks}
        catalog_id = "t0" if "t0" not in ids else "catalog"
        catalog = {
            "id": catalog_id,
            "intent": "list available HCLI tools",
            "acceptance": "output of huckle cli ls has been observed",
            "depends_on": [],
            "status": "pending",
            "observations": [],
            "hint": "First bash must be exactly: huckle cli ls",
        }
        for task in tasks:
            deps = task.get("depends_on") or []
            if catalog_id not in deps:
                deps = [catalog_id] + deps
            task["depends_on"] = deps
        return [catalog] + tasks

    def _plan_tasks(self, goal, blocker):
        last = None
        critique = None
        for attempt in range(MAX_REPLANS + 1):
            prompt = self._planner_prompt(goal, blocker, critique, last)
            last = self._complete([
                {"role": "system", "content": self._system_prompt()},
                {"role": "user", "content": prompt},
            ])
            if last is None:
                return None, "no model output while planning"
            tasks, critique = self._parse_tasks(last, goal)
            if tasks is not None:
                return tasks, None
        return None, critique or "could not produce a task list"

    def _system_prompt(self):
        allowed = "git, pwd, ls, echo, grep, curl, cat, head, tail, wc, man, hat, huckle, ddgr"
        return (
            self.behavior
            + "\n\n# Live constraints (orchestrator-enforced)\n"
            + "Break the user goal into ordered tasks. Do not emit bash.\n"
            + "Each task must be doable with one-command steps from: " + allowed + ".\n"
            + "At most %d tasks. Prefer fewer.\n" % MAX_TASKS
            + "Output one JSON object and nothing else:\n"
            + '{"goal":"<user goal>","tasks":[{"intent":"<one observable step>","acceptance":"<what observation finishes it>","depends_on":[]}]}\n'
            + "No markdown. No text before or after the object.\n"
        )

    def _planner_prompt(self, goal, blocker, critique, last):
        doc = self._plan_state()
        done = []
        for task in doc.get("tasks") or []:
            if task.get("status") == "done":
                done.append("%s: %s" % (task.get("intent"), task.get("result") or "done"))
        parts = ["user goal:\n" + (goal or "")]
        if done:
            parts.append("already done:\n" + "\n".join(done))
        if blocker:
            parts.append("blocker:\n" + blocker)
            parts.append("Replace only the remaining work. Do not repeat tasks already done.")
        if critique:
            prev = last if last and len(last) <= 800 else ((last or "")[:800] + "\n...")
            parts.append("previous output rejected: %s\n%s" % (critique, prev))
        parts.append("Emit the task list for the remaining work. No bash.")
        return "\n\n".join(parts)

    def _parse_tasks(self, text, fallback_goal):
        raw = (text or "").strip()
        if not raw:
            return None, "empty planner output"

        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
            raw = raw.strip()

        start, end = raw.find("{"), raw.rfind("}")
        if start == -1 or end <= start:
            return None, "planner output had no json object"

        try:
            obj = json.loads(raw[start:end + 1])
        except ValueError as e:
            return None, "invalid planner json: %s" % e

        if not isinstance(obj, dict):
            return None, "planner output must be an object"
        tasks = obj.get("tasks")
        if not isinstance(tasks, list) or not tasks:
            return None, "planner output needs a non-empty tasks array"

        normalized = []
        seen = set()
        for i, item in enumerate(tasks[:MAX_TASKS], 1):
            if not isinstance(item, dict):
                return None, "task %d is not an object" % i
            intent = item.get("intent")
            if not isinstance(intent, str) or not intent.strip():
                return None, "task %d needs a string intent" % i
            acceptance = item.get("acceptance") or ""
            if not isinstance(acceptance, str):
                acceptance = str(acceptance)
            deps = self._coerce_deps(item.get("depends_on"))
            task_id = item.get("id")
            if not isinstance(task_id, str) or not task_id.strip() or task_id in seen:
                task_id = "t%d" % i
            seen.add(task_id)
            normalized.append({
                "id": task_id,
                "intent": intent.strip(),
                "acceptance": acceptance.strip() or "observations answer the intent",
                "depends_on": deps,
                "status": "pending",
                "observations": [],
                "hint": "",
            })

        known = {t["id"] for t in normalized}
        for task in normalized:
            task["depends_on"] = [dep for dep in task["depends_on"] if dep in known and dep != task["id"]]

        if not (obj.get("goal") or fallback_goal):
            return None, "planner output needs a goal"
        return normalized, None

    def _coerce_deps(self, value):
        if value is None or value == "":
            return []
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        if isinstance(value, dict):
            dep = value.get("id") or value.get("intent") or ""
            return [str(dep)] if dep else []
        if isinstance(value, (list, tuple)):
            deps = []
            for item in value:
                if isinstance(item, str) and item.strip():
                    deps.append(item.strip())
                elif isinstance(item, dict):
                    dep = item.get("id") or item.get("intent") or ""
                    if dep:
                        deps.append(str(dep))
                elif isinstance(item, (int, float)) and not isinstance(item, bool):
                    deps.append(str(item))
            return deps
        return []

    def _complete(self, messages):
        model = self.config.model
        if self.config.provider is None or model is None:
            log.warning("no provider or model selected. select from the list of available providers and models.")
            return None

        if self.config.provider == "ollama":
            client = openai.OpenAI(base_url=self.config.ollama_service_url, api_key="ollama")
        elif self.config.provider == "xai":
            client = openai.OpenAI(api_key=os.getenv("XAI_API_KEY"), base_url="https://api.x.ai/v1")
        else:
            log.error("no provider selected. select from the list of available providers.")
            return None

        kwargs = dict(model=model, messages=messages)
        try:
            response = client.chat.completions.create(response_format={"type": "json_object"}, **kwargs)
        except Exception as e:
            log.debug("json response_format unsupported; retrying without it: %s" % e)
            response = client.chat.completions.create(**kwargs)

        text = response.choices[0].message.content
        log.info(text)
        return text
