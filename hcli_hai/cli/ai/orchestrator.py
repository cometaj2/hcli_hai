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
from ai import plan as p
from ai.policy import (
    bash_error, empty_step, next_runnable, observe_key, alloc_id,
    derive_check, evidence_met, task_has_key, listing_files, check_key,
)
from ai.router import skill_area
from pathlib import Path
from hcli_problem_details import ConflictError

log = logger.Logger()

MAX_TASKS = 100   # 8
MAX_REPLANS = 10  # 2
MAX_ADVANCE = 100 # 8

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
            self.plan = p.Plan()
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
            self.plan.clear()
            if should_vibe is True:
                log.info("orchestrator started.")
            else:
                log.info("orchestrator stopped.")

    def is_vibing(self):
        with self.rlock:
            return self._is_vibing

    def pending_bash(self):
        with self.rlock:
            public = self.plan.public()
            if public.get("status") != "continue":
                return None
            bash = (public.get("bash") or "").strip()
            return bash or None

    def status(self):
        with self.rlock:
            if not self._is_vibing:
                return "inactive"
            public = self.plan.public()
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
        return self.plan.get_step()

    def public(self):
        return self.plan.dumps()

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
            self.plan.clear()
            self._skill_area, self._skill = self._load_skill(user)
            log.info("orchestrator skill: %s" % (self._skill_area or "none"))
            self._set_plan_status("planning", user)
            tasks, critique = self._plan_tasks(user, None)
            if not tasks:
                return self._finish("need_help", critique or "could not break the goal into tasks")

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
            self.plan.append_observation(pending, observation or "")
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
            if not self.plan.has_observation(pending):
                msg = "hai agent next has not recorded an observation for this step"
                log.error(msg)
                raise ConflictError(detail=msg)
            log.info("orchestrator marking step done: %s" % pending)
            self._set_plan_status("planning")
            return self._run_until_blocked()

    def _plan_state(self):
        return self.plan.get_state()

    def _write(self, plan):
        self.plan.replace(plan)

    def _set_plan_status(self, plan_status, goal=None):
        plan = self._plan_state()
        plan["plan_status"] = plan_status
        if goal is not None:
            plan["goal"] = goal
        plan["step"] = empty_step()
        self._write(plan)

    def _install(self, goal, tasks, replans):
        plan = self._plan_state()
        plan["goal"] = goal
        plan["plan_status"] = "active"
        plan["tasks"] = tasks
        plan["cursor"] = ""
        plan["step"] = empty_step()
        plan["say"] = ""
        plan["replans"] = replans
        plan["skill_area"] = getattr(self, "_skill_area", "") or ""
        plan["skill"] = getattr(self, "_skill", "") or ""
        self._write(plan)
        log.info("orchestrator installed %d task(s)." % len(tasks))

    def _run_until_blocked(self):
        advances = 0
        while advances < MAX_ADVANCE:
            plan = self._compile(self._plan_state())
            self._write(plan)
            task = next_runnable(plan.get("tasks") or [])
            if task is None:
                return self._finish("done", self._summary(plan))

            task["status"] = "active"
            plan["cursor"] = task.get("id") or ""
            plan["plan_status"] = "planning"
            plan["step"] = empty_step()
            self._write(plan)
            log.info("orchestrator task %s active: %s" % (task.get("id"), task.get("intent")))

            if evidence_met(task):
                self._close_task(task.get("id"), self._learned(task))
                continue

            forced = self._forced_bash(plan, task)
            if forced:
                log.info("orchestrator evidence command for %s: %s" % (task.get("id"), forced))
                return self._emit_continue(task, forced, "evidence check")

            advances += 1
            own = list(task.get("observations") or [])
            step = self.runner.step(self._with_prior(plan, task), own, self._prior_keys(plan, task))
            if step is None:
                step = {
                    "status": "need_help",
                    "goal": task.get("intent") or "",
                    "why": "",
                    "bash": "",
                    "say": "runner produced no step",
                }
            step["goal"] = task.get("intent") or step.get("goal") or ""

            if step.get("status") == "done":
                if evidence_met(task):
                    self._close_task(task.get("id"), self._learned(task))
                    continue
                if (task.get("check") or {}).get("kind") != "observe" and own:
                    self._close_task(task.get("id"), step.get("say") or self._learned(task))
                    continue
                step = {
                    "status": "need_help",
                    "goal": task.get("intent") or "",
                    "why": "",
                    "bash": "",
                    "say": "done rejected; this task has no evidence of its own",
                }

            if step.get("status") == "continue":
                bash = (step.get("bash") or "").strip()
                critique = bash_error(bash)
                if not critique and self._redundant(plan, bash):
                    step = {
                        "status": "need_help",
                        "goal": task.get("intent") or "",
                        "why": "",
                        "bash": "",
                        "say": "already observed %s; not evidence for this task" % bash,
                    }
                    critique = None
                if not critique and step.get("status") == "continue":
                    missing = self._unlisted_path(plan, step.get("bash"))
                    if missing:
                        step = {
                            "status": "need_help",
                            "goal": task.get("intent") or "",
                            "why": "",
                            "bash": "",
                            "say": "path %s was not in an observed listing" % missing,
                        }
                if critique:
                    step = {
                        "status": "need_help",
                        "goal": task.get("intent") or "",
                        "why": "",
                        "bash": "",
                        "say": critique,
                    }
                elif step.get("status") == "continue":
                    return self._emit_continue(task, step.get("bash"), step.get("why") or "")

            plan = self._plan_state()
            current = self._task(plan, task.get("id"))
            if current is None:
                return self._finish("need_help", "active task disappeared")
            return self._on_need_help(step.get("say") or "task blocked")

        return self._finish("need_help", "advanced too many tasks without a client command")

    def _emit_continue(self, task, bash, why):
        plan = self._plan_state()
        current = self._task(plan, task.get("id"))
        if current is None:
            return self._finish("need_help", "active task disappeared")
        seq = int(plan.get("step_seq") or 0) + 1
        step = {
            "status": "continue",
            "goal": current.get("intent") or "",
            "why": why or "",
            "bash": bash,
            "say": "",
            "id": "s%d" % seq,
            "claimed": False,
        }
        current["status"] = "active"
        plan["cursor"] = current.get("id") or ""
        plan["plan_status"] = "active"
        plan["step"] = step
        plan["step_seq"] = seq
        plan["say"] = ""
        self._write(plan)
        log.info("orchestrator waiting for client to run: %s" % bash)
        return self.public()

    def _close_task(self, task_id, result):
        plan = self._plan_state()
        current = self._task(plan, task_id)
        if current is None:
            return
        current["status"] = "done"
        current["result"] = result or ""
        plan["cursor"] = current.get("id") or ""
        plan["step"] = empty_step()
        plan["plan_status"] = "active"
        self._write(plan)
        log.info("orchestrator task %s done." % current.get("id"))

    def _on_need_help(self, blocker):
        plan = self._plan_state()
        replans = int(plan.get("replans") or 0)
        goal = plan.get("goal") or ""
        if replans >= MAX_REPLANS:
            return self._finish("need_help", blocker)

        log.info("orchestrator replanning (%d/%d): %s" % (replans + 1, MAX_REPLANS, blocker))
        plan["plan_status"] = "planning"
        plan["replans"] = replans + 1
        self._write(plan)

        tasks, critique = self._plan_tasks(goal, blocker)
        if not tasks:
            return self._finish("need_help", critique or blocker)

        done = [t for t in (plan.get("tasks") or []) if t.get("status") == "done"]
        used = {t.get("id") for t in done}
        fresh = []
        for task in tasks:
            if self._covered(done, task):
                continue
            if not task.get("id") or task.get("id") in used:
                task["id"] = alloc_id(used)
            else:
                used.add(task.get("id"))
            fresh.append(task)
        if not fresh:
            return self._finish("need_help", blocker)

        known = used | {t.get("id") for t in fresh}
        for task in fresh:
            task["depends_on"] = [dep for dep in (task.get("depends_on") or []) if dep in known and dep != task.get("id")]
        self._install(goal, done + fresh, replans + 1)
        return self._run_until_blocked()

    def _redundant(self, plan, bash):
        wanted = self._observe_key(bash)
        if wanted is None:
            return False
        for task in plan.get("tasks") or []:
            for item in task.get("observations") or []:
                if self._observe_key(item.get("bash")) == wanted:
                    return True
        return False

    def _observe_key(self, bash):
        return observe_key(bash)

    def _covered(self, done, task):
        key = check_key(task)
        if key and any(task_has_key(item, key) for item in done):
            return True
        intent = (task.get("intent") or "").strip().lower()
        return any((item.get("intent") or "").strip().lower() == intent for item in done)

    def _forced_bash(self, plan, task):
        check = task.get("check") or {}
        if check.get("kind") != "observe":
            return None
        bash = (check.get("bash") or "").strip()
        if not bash or bash_error(bash):
            return None
        if task_has_key(task, check_key(task)):
            return None
        key = check_key(task) or ()
        if key and key[0] == "read":
            if not self._listings(plan):
                return None
            if self._unlisted_path(plan, bash):
                return None
        return bash

    def _listings(self, plan):
        found = []
        for task in plan.get("tasks") or []:
            for item in task.get("observations") or []:
                if (item.get("bash") or "").split()[:1] == ["ls"]:
                    found.append(item.get("result") or "")
        return found

    def _prior_keys(self, plan, task):
        keys = []
        for other in plan.get("tasks") or []:
            if other.get("id") == task.get("id"):
                continue
            for item in other.get("observations") or []:
                key = observe_key(item.get("bash"))
                if key and key not in keys:
                    keys.append(key)
        return [" ".join(key) for key in keys]

    def _compile(self, plan):
        if (plan.get("skill_area") or "") != "LOCAL":
            return plan
        listings = self._listings(plan)
        if not listings:
            return plan
        names = []
        for blob in listings:
            for name in listing_files(blob):
                if name not in names:
                    names.append(name)
        tasks = plan.get("tasks") or []
        covered = set()
        for task in tasks:
            key = check_key(task)
            if key and key[0] == "read" and len(key) > 1:
                covered.add(key[1])
        listing_id = ""
        for task in tasks:
            if task_has_key(task, ("ls",)):
                listing_id = task.get("id") or ""
                break
        used = {task.get("id") for task in tasks}
        for name in names:
            if name in covered or len(tasks) >= MAX_TASKS:
                continue
            task_id = alloc_id(used)
            tasks.append({
                "id": task_id,
                "intent": "read %s" % name,
                "acceptance": "contents of %s" % name,
                "depends_on": [listing_id] if listing_id else [],
                "status": "pending",
                "observations": [],
                "hint": "",
                "check": {"kind": "observe", "key": ["read", name], "bash": "cat %s" % name},
            })
            covered.add(name)
            log.info("orchestrator compiled read task %s for %s" % (task_id, name))
        plan["tasks"] = tasks
        return plan

    def _unlisted_path(self, plan, bash):
        parts = (bash or "").split()
        if not parts or parts[0] not in ("cat", "head", "tail", "grep"):
            return None
        args = [p for p in parts[1:] if not p.startswith("-") and not p.isdigit()]
        if not args:
            return None
        path = args[-1]
        name = path.rstrip("/").split("/")[-1]
        if not name or name in (".", ".."):
            return None
        listings = self._listings(plan)
        if not listings:
            return None
        blob = "\n".join(listings)
        if name not in blob.split():
            return path
        return None

    def _learned(self, task):
        obs = task.get("observations") or []
        if not obs:
            return "observed"
        result = (obs[-1].get("result") or "").strip()
        return result or "observed"

    def _already_observed(self, plan, bash):
        wanted = (bash or "").strip()
        if not wanted:
            return False
        for task in plan.get("tasks") or []:
            for item in task.get("observations") or []:
                if (item.get("bash") or "").strip() == wanted:
                    return True
        return False

    def _accrued(self, plan):
        out = []
        for task in plan.get("tasks") or []:
            for item in task.get("observations") or []:
                out.append({
                    "bash": item.get("bash") or "",
                    "result": item.get("result") or "",
                })
        return out

    def _with_prior(self, plan, task):
        call = dict(task)
        skill = (plan.get("skill") or "").strip()
        if skill:
            area = plan.get("skill_area") or "skill"
            hint = (call.get("hint") or "").strip()
            call["hint"] = ("Area skill (%s):\n%s\n%s" % (area, skill[:1200], hint)).strip()
        return call

    def _task(self, plan, task_id):
        for task in plan.get("tasks") or []:
            if task.get("id") == task_id:
                return task
        return None

    def _finish(self, status, say):
        plan = self._plan_state()
        plan["plan_status"] = status
        plan["say"] = say or ""
        plan["step"] = {
            "status": "done" if status == "done" else "need_help",
            "goal": plan.get("goal") or "",
            "why": "",
            "bash": "",
            "say": say or "",
        }
        plan["cursor"] = ""
        self._write(plan)
        self._join(plan["step"]["status"], say or "")
        log.info("orchestrator finished: %s" % status)
        return self.public()

    def _summary(self, plan):
        lines = []
        for task in plan.get("tasks") or []:
            if task.get("status") != "done":
                continue
            learned = (task.get("result") or "").strip()
            if learned:
                lines.append("%s: %s" % (task.get("intent") or task.get("id"), learned))
        if lines:
            return "\n".join(lines)
        return "The goal is done. No task reported a separate result."

    def _join(self, status, say):
        plan = self._plan_state()
        goal = (plan.get("goal") or "").strip()
        tasks = plan.get("tasks") or []

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
        plan = self._plan_state()
        done = []
        for task in plan.get("tasks") or []:
            if task.get("status") == "done":
                done.append("%s: %s" % (task.get("intent"), task.get("result") or "done"))
        parts = ["user goal:\n" + (goal or "")]
        if done:
            parts.append("already done:\n" + "\n".join(done))
        if blocker:
            parts.append("blocker:\n" + blocker)
            parts.append("Replace only the remaining work. Do not repeat tasks already done.")
        skill = (plan.get("skill") or getattr(self, "_skill", "") or "").strip()
        if skill:
            parts.append("area skill (%s):\n%s" % (plan.get("skill_area") or getattr(self, "_skill_area", "") or "skill", skill))
            parts.append("Follow the area skill. Do not emit bash. Do not copy the skill into a task intent.")
        if critique:
            prev = last if last and len(last) <= 800 else ((last or "")[:800] + "\n...")
            parts.append("previous output rejected: %s\n%s" % (critique, prev))
        parts.append("Emit the task list for the remaining work. No bash.")
        return "\n\n".join(parts)

    def _load_skill(self, goal):
        area = skill_area(goal)
        if not area:
            return "", ""
        path = os.path.join(self.config.dot_hai_skills, area + ".md")
        if not os.path.exists(path):
            path = os.path.join(os.path.dirname(__file__), "..", "skills", area + ".md")
        if not os.path.exists(path):
            return area, ""
        try:
            return area, Path(path).read_text(encoding="utf-8").strip()
        except OSError:
            return area, ""

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
            check = derive_check(intent, acceptance)
            normalized.append({
                "id": task_id,
                "intent": intent.strip(),
                "acceptance": acceptance.strip() or "observations answer the intent",
                "depends_on": deps,
                "status": "pending",
                "observations": [],
                "hint": "",
                "check": check,
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
