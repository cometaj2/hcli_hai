import logger
import threading
import os
import re
import json
import openai
import config as a
from ai import ai
from ai import plan as p
from ai.policy import bash_error, empty_step
from ai.provider import provider as pr
from pathlib import Path
from frontmatter import Frontmatter

log = logger.Logger()


class Orchestrator:
    """Bare-bones Pi-style harness.

    Holds a goal and a flat list of observations. On each advance, asks the model
    for the next single step (continue with one bash, or done / need_help).
    The client runs the bash and posts the observation back via next/mark.
    No task graph, no dependency tracking, no forced evidence, no reentrant replanning.
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
            self.provider = pr.Provider()
            self.plan = p.Plan()
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
            if should_vibe:
                log.info("orchestrator started.")
            else:
                log.info("orchestrator stopped.")

    def is_vibing(self):
        with self.rlock:
            return self._is_vibing

    def pending_bash(self):
        with self.rlock:
            public = self.plan.public()
            step = public.get("step") or {}
            if step.get("status") != "continue":
                return None
            bash = (step.get("bash") or "").strip()
            return bash or None

    def status(self):
        with self.rlock:
            if not self._is_vibing:
                return "inactive"
            public = self.plan.public()
            step = public.get("step") or {}
            if step.get("status") == "continue" and step.get("bash"):
                return "next"
            plan_status = public.get("plan_status") or "idle"
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

            log.info("orchestrator starting bare-bones harness.")
            self.plan.clear()
            self._set_plan_status("planning", user)
            return self._advance()

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
            # next() has already stored the observation for the pending bash.
            # Clear the pending step so _advance can emit a new one or finish.
            self.plan.clear_step()
            log.info("orchestrator advancing after observation.")
            return self._advance()

    def _set_plan_status(self, plan_status, goal=None):
        plan = self.plan.get_state()
        plan["plan_status"] = plan_status
        if goal is not None:
            plan["goal"] = goal
        plan["step"] = empty_step()
        self.plan.replace(plan)

    def _advance(self):
        """Ask the model for the next step given the full observation history.
        Never emit a command that has already been executed.
        """
        plan = self.plan.get_state()
        goal = plan.get("goal") or ""
        observations = plan.get("observations") or []

        log.info("orchestrator advancing with %d observation(s)." % len(observations))

        already = self._executed_commands(observations)

        step = self._next_step(goal, observations, already)
        if step is None:
            return self._finish("need_help", "no model output")

        step["goal"] = goal

        if step.get("status") == "continue":
            bash = self._norm(step.get("bash"))
            critique = bash_error(bash)
            if critique:
                return self._finish("need_help", critique)
            if bash in already:
                # One re-prompt only. Not a replan.
                log.info("model proposed already-executed command: %s; re-prompting once" % bash)
                step = self._next_step(goal, observations, already, forbidden=bash)
                if not step or step.get("status") != "continue":
                    return self._finish("need_help", "model could not propose a new command after repeating %s" % bash)
                bash = self._norm(step.get("bash"))
                critique = bash_error(bash)
                if critique or bash in already:
                    return self._finish(
                        "need_help",
                        "model repeated an already-executed command: %s" % bash,
                    )
            return self._emit_continue(bash, step.get("why") or "")

        return self._finish(step.get("status"), step.get("say") or "")

    def _norm(self, bash):
        """Normalize a command for duplicate detection."""
        return " ".join((bash or "").split())

    def _executed_commands(self, observations):
        """Normalized set of every bash command that has already been run."""
        executed = set()
        for item in observations or []:
            bash = self._norm(item.get("bash"))
            if bash:
                executed.add(bash)
        return executed

    def _load_selected_skill(self):
        skill = getattr(self.config, "skill", None)
        if not skill:
            return ""

        skills_root = Path(self.config.dot_hai_skills).resolve()
        skill_dir = (skills_root / skill).resolve()
        skill_file = skill_dir / "SKILL.md"

        # Prevent a skill value from escaping the skills directory.
        try:
            skill_file.relative_to(skills_root)
        except ValueError:
            log.warning("invalid skill path: %s", skill)
            return ""

        if not skill_file.is_file():
            log.warning("selected skill not found: %s", skill_file)
            return ""

        try:
            return skill_file.read_text(encoding="utf-8")
        except OSError as e:
            log.warning("could not read selected skill %s: %s", skill_file, e)
            return ""

    def _next_step(self, goal, observations, already=None, forbidden=None):
        """Direct model call with explicit history. No AgentRunner task/evidence scaffolding."""
        already = already or set()
        history = []
        for i, item in enumerate(observations, 1):
            bash = self._norm(item.get("bash"))
            result = (item.get("result") or "").strip()
            history.append("%d. ran: %s\n   result:\n%s" % (i, bash, result or "(empty)"))

        history_text = "\n\n".join(history) if history else "(no observations yet)"
        already_text = ", ".join(sorted(already)) if already else "(none)"

        skill_text = self._load_selected_skill()

        skill_block = ""
        if skill_text:
            skill_block = (
                "\n\n# Selected skill\n"
                "The following SKILL.md contains instructions and procedures for this task. "
                "Use it when deciding the next command. Follow the harness constraints below "
                "even if the skill suggests otherwise.\n"
                "<skill>\n"
                + skill_text
                + "\n</skill>"
            )

        system = (
            self.behavior
            + skill_block
            + "\n\n# Live constraints (enforced by harness)\n"
            + "Emit exactly one next step as a JSON object.\n"
            + "If there are no observations yet, you MUST emit status=continue with the first useful command.\n"
            + "Do not emit need_help merely because nothing has been observed.\n"
            + "Never re-emit a command that has already been executed.\n"
            + "Output one JSON object with keys status, goal, why, bash, say. Nothing else."
        )

        user = (
            "Goal:\n%s\n\n"
            "Observations so far:\n%s\n\n"
            "Already executed commands (do not repeat any of these):\n%s\n\n"
            "What is the single next step?\n"
            "- If observations is empty, emit status=continue with the first command that advances the goal.\n"
            "- Do not repeat any command listed under already executed.\n"
            "- Prefer status=done only when the observations already answer the goal."
            % (goal, history_text, already_text)
        )
        if forbidden:
            user += (
                "\n\nYour previous proposal %r was rejected because it was already executed. "
                "Choose a different command, or status=done if the goal is satisfied."
                % forbidden
            )

        text = self._complete([
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ])
        if text is None:
            return None

        return self._parse_step(text)

    def _parse_step(self, text):
        raw = (text or "").strip()
        if not raw:
            return None

        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
            raw = raw.strip()

        start, end = raw.find("{"), raw.rfind("}")
        if start == -1 or end <= start:
            return None

        try:
            obj = json.loads(raw[start:end + 1])
        except ValueError:
            return None

        if not isinstance(obj, dict):
            return None

        status = obj.get("status")
        if status not in ("continue", "done", "need_help"):
            return None

        return {
            "status": status,
            "goal": str(obj.get("goal") or ""),
            "why": str(obj.get("why") or ""),
            "bash": str(obj.get("bash") or "").strip(),
            "say": str(obj.get("say") or "").strip(),
        }

    def _complete(self, messages):
        kwargs = dict(model=self.provider.get_model(), messages=messages)
        try:
            response = self.provider.client.chat.completions.create(response_format={"type": "json_object"}, **kwargs)
        except Exception as e:
            log.debug("json response_format unsupported; retrying without it: %s" % e)
            response = self.provider.client.chat.completions.create(**kwargs)

        text = response.choices[0].message.content
        log.info("model step: %s" % text)
        return text

    def _emit_continue(self, bash, why):
        plan = self.plan.get_state()
        step = {
            "status": "continue",
            "goal": plan.get("goal") or "",
            "why": why or "",
            "bash": bash,
            "say": "",
        }
        plan["step"] = step
        plan["plan_status"] = "active"
        plan["say"] = ""
        self.plan.replace(plan)
        log.info("orchestrator waiting for client to run: %s" % bash)
        return self.public()

    def _finish(self, status, say):
        plan = self.plan.get_state()
        plan["plan_status"] = status
        plan["say"] = say or ""
        plan["step"] = {
            "status": "done" if status == "done" else "need_help",
            "goal": plan.get("goal") or "",
            "why": "",
            "bash": "",
            "say": say or "",
        }
        self.plan.replace(plan)
        self._join(plan["step"]["status"], say or "")
        log.info("orchestrator finished: %s" % status)
        return self.public()

    def _join(self, status, say):
        # Minimal join back into the conversation. Keep the existing hijack pattern
        # so the chat transcript sees the result; clean this up later if desired.
        plan = self.plan.get_state()
        goal = (plan.get("goal") or "").strip()
        observations = plan.get("observations") or []

#         if status == "done":
#             header = "**Task completed.**"
#         else:
#             header = "**Agent needs help.**"
# 
#         parts = [header]
#         if goal:
#             parts.append("**Goal:** %s" % goal)

        parts = []
        trace = []
        for i, item in enumerate(observations, 1):
            cmd = (item.get("bash") or "").strip()
            result = (item.get("result") or "").strip()
            trace.append("%d. `%s`\n > %s" % (i, cmd, result))
        if trace:
            parts.append("**Execution trace:**\n" + "\n\n".join(trace))

#         parts.append("**Result:**\n%s" % (say or ""))

        inceptive_user_message = self.ai.contextmgr.context._messages[-1]["content"]
        self.ai.contextmgr.context._messages.pop()
        harnessed_user_message = inceptive_user_message + "\n\n" + "\n\n".join(parts)

        self.ai.consume_request(harnessed_user_message)
        response = self.ai.process_request()

        if response is None:
            return None

        self.ai.commit_response(response)

    # list all skills
    def list_skills(self):
        skills = []
        etc = self.config.dot_hai_skills

        # Iterate through all items in the folder
        for item in os.listdir(etc):
            item_path = os.path.join(etc, item)
            # Check if the item is a directory
            if os.path.isdir(item_path):
                skill_file_path = os.path.join(item_path, 'SKILL.md')
                if os.path.exists(skill_file_path):
                    try:
                        post = Frontmatter.read_file(skill_file_path)
                        skills.append({
                            'skill_id': Path(item_path).name,
                            'description': post['attributes']['description'].rstrip()
                        })
                    except json.JSONDecodeError:
                        skills.append({
                            'skill_id': Path(item_path).name,
                            'description': 'N/A'
                        })
                else:
                    skills.append({
                        'skill_id': Path(item_path).name,
                        'description': 'N/A'
                    })
        return skills

    def get_skill(self, skill):
        return self.config.skill

    def set_skill(self, skill):
        with self.rlock:
            self.config.skill = skill
            log.info(f"skill id {skill} is set.")
