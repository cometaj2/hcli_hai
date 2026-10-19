import json
import os
import re
import threading
import traceback
from pathlib import Path

from frontmatter import Frontmatter
from hcli_problem_details import BadRequestError

import config as a
import logger
from ai import ai
from ai import plan as p
from ai.policy import bash_error, empty_step
from ai.provider import provider as pr
from ai.runstore import RunStore


log = logger.Logger()


class Orchestrator:
    """
    Bare-bones, client-mediated agent harness.

    The orchestrator plans one command at a time. It never executes shell
    commands directly. An HCLI client executes the proposed command and sends
    the resulting observation back through next().

    Active planning state is held in Plan. Durable execution history is held
    separately in RunStore as append-only JSONL:

        <context-id>/
            context.json
            runs/
                <run-id>/
                    trace.jsonl
                    context.before.json
                    context.after.json
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
            self.ai = ai.AI()
            self.provider = pr.Provider()
            self.plan = p.Plan()
            self.run_store = RunStore()

            self._is_vibing = False
            self._active_run_id = None
            self._active_context_id = None

            self.behavior = self._load_behavior()
            self.system = None

            self.initialized = True

    # ------------------------------------------------------------------
    # Behavior and skill loading
    # ------------------------------------------------------------------

    def _load_behavior(self):
        path = self.config.dot_hai_orchestrator_file

        if os.path.exists(path):
            return Path(path).read_text(encoding="utf-8")

        return a.ORCHESTRATOR_MD

    def _load_selected_skill(self):
        skill = getattr(self.config, "skill", None)

        if not skill:
            return ""

        skills_root = Path(
            self.config.dot_hai_skills
        ).resolve()

        skill_dir = (skills_root / skill).resolve()
        skill_file = skill_dir / "SKILL.md"

        # Prevent a skill value from escaping the skills directory.
        try:
            skill_file.relative_to(skills_root)
        except ValueError:
            log.warning("invalid skill path: %s", skill)
            return ""

        if not skill_file.is_file():
            log.warning(
                "selected skill not found: %s",
                skill_file,
            )
            return ""

        try:
            return skill_file.read_text(
                encoding="utf-8",
            )
        except OSError as error:
            log.warning(
                "could not read selected skill %s: %s",
                skill_file,
                error,
            )
            return ""

    def _load_harness_system(self):
        with self.rlock:
            if self.system is not None:
                return self.system

            skill_text = self._load_selected_skill()
            skill_block = ""

            if skill_text:
                skill_block = (
                    "\n\n# Selected skill\n"
                    "The following SKILL.md contains instructions and "
                    "procedures for this task. Use it when deciding the "
                    "next command. Follow the harness constraints below "
                    "even if the skill suggests otherwise.\n"
                    "<skill>\n"
                    + skill_text
                    + "\n</skill>"
                )

            self.system = (
                self.behavior
                + skill_block
                + "\n\n# Live constraints (enforced by harness)\n"
                + "Emit exactly one next step as a JSON object.\n"
                + "If there are no observations yet, you MUST emit "
                + "status=continue with the first useful command.\n"
                + "Do not emit need_help merely because nothing has "
                + "been observed.\n"
                + "Never re-emit a command that has already been executed.\n"
                + "Output one JSON object with keys status, goal, why, "
                + "bash, say. Nothing else."
            )

            return self.system

    def _load_skill_system(self):
        skill_text = self._load_selected_skill()
        skill_block = ""

        if skill_text:
            skill_block = (
                "\n\n# Selected skill\n"
                "The following SKILL.md contains instructions and "
                "procedures for this task. Use it when meeting the "
                "user's stated goal.\n"
                "<skill>\n"
                + skill_text
                + "\n</skill>"
            )

        return skill_block

    # ------------------------------------------------------------------
    # Agent state
    # ------------------------------------------------------------------

    def set_vibe(self, should_vibe):
        with self.rlock:
            self._is_vibing = should_vibe
            self.plan.clear()

            if should_vibe:
                log.info("orchestrator started.")
            else:
                log.info("orchestrator stopped.")
                self.system = None
                self._active_run_id = None
                self._active_context_id = None

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

    # ------------------------------------------------------------------
    # Durable execution trace handling
    # ------------------------------------------------------------------

    def _trace_context_id(self):
        """
        Keep all trace and snapshot files associated with the context in
        which the run started.
        """
        return (
            self._active_context_id
            or self.ai.current()
        )

    def _originating_message_reference(self):
        """
        Return the index and content hash of the user message that initiated
        the current run.

        Existing context messages remain compatible with the provider because
        no application-specific fields are added to the message dictionaries.
        """
        messages = self.ai.contextmgr.messages() or []

        if not messages:
            return None, None

        message_index = len(messages) - 1
        content = messages[message_index].get("content") or ""

        return (
            message_index,
            self.run_store.message_sha256(content),
        )

    def _start_trace(self, goal):
        context_id = self.ai.current()

        message_index, message_sha256 = (
            self._originating_message_reference()
        )

        run_id = self.run_store.create_run(
            config=self.config,
            context_id=context_id,
            goal=goal,
            originating_message_index=message_index,
            originating_message_sha256=message_sha256,
        )

        self._active_run_id = run_id
        self._active_context_id = context_id

        self.plan.set_run_id(run_id)

        # The user request has already been added to the active context by
        # AI.consume_request() before harness() is called.
        self.run_store.snapshot_before(
            config=self.config,
            context_id=context_id,
            run_id=run_id,
            context=self.ai.get_context(),
        )

        return run_id

    def _trace_append(self, event_type, event):
        run_id = self._active_run_id

        if not run_id:
            return

        self.run_store.append(
            config=self.config,
            context_id=self._trace_context_id(),
            run_id=run_id,
            event_type=event_type,
            event=event,
        )

    def _trace_finish(
        self,
        status,
        summary="",
        response_message_index=None,
        context_after=None,
        response_status=None,
    ):
        run_id = self._active_run_id

        if not run_id:
            return

        event = {
            "status": status,
            "summary": summary or "",
            "response_message_index": (
                response_message_index
            ),
            "response_status": response_status,
        }

        if context_after is not None:
            event["context_after"] = context_after

        self.run_store.append(
            config=self.config,
            context_id=self._trace_context_id(),
            run_id=run_id,
            event_type="run_finished",
            event=event,
        )

    def list_traces(self):
        return self.run_store.list_runs(
            config=self.config,
            context_id=self.ai.current(),
        )

    def trace(self, run_id=None):
        run_id = run_id or self._active_run_id

        if not run_id:
            return []

        return self.run_store.read(
            config=self.config,
            context_id=self.ai.current(),
            run_id=run_id,
        )

    def trace_jsonl(self, run_id=None):
        run_id = run_id or self._active_run_id

        if not run_id:
            return ""

        return self.run_store.read_jsonl(
            config=self.config,
            context_id=self.ai.current(),
            run_id=run_id,
        )

    # ------------------------------------------------------------------
    # Planning lifecycle
    # ------------------------------------------------------------------

    def harness(self):
        with self.lock:
            if not self.is_vibing():
                return None

            if self.pending_bash():
                log.info(
                    "orchestrator already waiting for hai agent next."
                )
                return self.public()

            messages = self.ai.contextmgr.messages() or []
            user = ""

            if messages:
                user = messages[-1].get("content") or ""

            user = user.strip()

            if not user:
                return self._finish(
                    "need_help",
                    "no user goal to plan",
                )

            log.info(
                "orchestrator starting bare-bones harness."
            )

            self.plan.clear()
            self._active_run_id = None
            self._active_context_id = None

            self._start_trace(user)
            self._set_plan_status("planning", user)

            self._trace_append(
                "planning_started",
                {
                    "goal": user,
                },
            )

            return self._advance()

    def next(self, observation):
        with self.lock:
            if not self.is_vibing():
                log.warning(
                    "agent is not running; refusing next."
                )
                return None

            pending = self.pending_bash()

            if not pending:
                log.warning(
                    "no pending bash step to observe; refusing next."
                )
                return None

            observation = observation or ""

            log.info(
                "orchestrator stored observation for: %s",
                pending,
            )

            self.plan.append_observation(
                pending,
                observation,
            )

            plan = self.plan.get_state()
            sequence = len(
                plan.get("observations") or []
            )

            self._trace_append(
                "command_observed",
                {
                    "sequence": sequence,
                    "bash": pending,
                    "result": observation,
                    "result_sha256": (
                        self.run_store.message_sha256(
                            observation,
                        )
                    ),
                },
            )

            return self.public()

    def mark(self):
        with self.lock:
            if not self.is_vibing():
                log.warning(
                    "agent is not running; refusing mark."
                )
                return None

            # next() stores the observation. mark() clears the pending step
            # and advances the planner.
            self.plan.clear_step()

            log.info(
                "orchestrator advancing after observation."
            )

            return self._advance()

    def _set_plan_status(self, plan_status, goal=None):
        plan = self.plan.get_state()

        plan["plan_status"] = plan_status

        if goal is not None:
            plan["goal"] = goal

        plan["step"] = empty_step()
        self.plan.replace(plan)

    def _advance(self):
        """
        Ask the model for the next step given the full observation history.

        The model must never emit a command that has already been executed.
        """
        plan = self.plan.get_state()
        goal = plan.get("goal") or ""
        observations = plan.get("observations") or []

        log.info(
            "orchestrator advancing with %d observation(s).",
            len(observations),
        )

        already = self._executed_commands(observations)

        step = self._next_step(
            goal,
            observations,
            already,
        )

        if step is None:
            return self._finish(
                "need_help",
                "no model output",
            )

        step["goal"] = goal

        if step.get("status") == "continue":
            bash = self._norm(step.get("bash"))
            critique = bash_error(bash)

            if critique:
                return self._finish(
                    "need_help",
                    critique,
                )

            if bash in already:
                # One re-prompt only. This is not a replan.
                log.info(
                    "model proposed already-executed command: %s; "
                    "re-prompting once",
                    bash,
                )

                step = self._next_step(
                    goal,
                    observations,
                    already,
                    forbidden=bash,
                )

                if (
                    not step
                    or step.get("status") != "continue"
                ):
                    return self._finish(
                        "need_help",
                        "model could not propose a new command "
                        "after repeating %s" % bash,
                    )

                bash = self._norm(step.get("bash"))
                critique = bash_error(bash)

                if critique or bash in already:
                    return self._finish(
                        "need_help",
                        "model repeated an already-executed "
                        "command: %s" % bash,
                    )

            return self._emit_continue(
                bash,
                step.get("why") or "",
            )

        return self._finish(
            step.get("status"),
            step.get("say") or "",
        )

    # ------------------------------------------------------------------
    # Planning/model interaction
    # ------------------------------------------------------------------

    def _norm(self, bash):
        """
        Normalize a command for duplicate detection.
        """
        return " ".join((bash or "").split())

    def _executed_commands(self, observations):
        """
        Return normalized commands that have already been executed.
        """
        executed = set()

        for item in observations or []:
            bash = self._norm(item.get("bash"))

            if bash:
                executed.add(bash)

        return executed

    def _next_step(
        self,
        goal,
        observations,
        already=None,
        forbidden=None,
    ):
        """
        Direct model call with explicit history.

        This harness deliberately does not use AgentRunner task/evidence
        scaffolding or a task graph.
        """
        already = already or set()
        history = []

        for index, item in enumerate(observations, 1):
            bash = self._norm(item.get("bash"))
            result = (item.get("result") or "").strip()

            history.append(
                "%d. ran: %s\n   result:\n%s"
                % (
                    index,
                    bash,
                    result or "(empty)",
                )
            )

        history_text = (
            "\n\n".join(history)
            if history
            else "(no observations yet)"
        )

        already_text = (
            ", ".join(sorted(already))
            if already
            else "(none)"
        )

        user = (
            "Goal:\n%s\n\n"
            "Observations so far:\n%s\n\n"
            "Already executed commands "
            "(do not repeat any of these):\n%s\n\n"
            "What is the single next step?\n"
            "- If observations is empty, emit status=continue "
            "with the first command that advances the goal.\n"
            "- Do not repeat any command listed under already "
            "executed.\n"
            "- Prefer status=done only when the observations "
            "already answer the goal."
            % (
                goal,
                history_text,
                already_text,
            )
        )

        if forbidden:
            user += (
                "\n\nYour previous proposal %r was rejected because "
                "it was already executed. Choose a different command, "
                "or status=done if the goal is satisfied."
                % forbidden
            )

        text = self._complete(
            [
                {
                    "role": "system",
                    "content": self._load_harness_system(),
                },
                {
                    "role": "user",
                    "content": user,
                },
            ]
        )

        if text is None:
            return None

        return self._parse_step(text)

    def _complete(self, messages):
        kwargs = {
            "model": self.provider.get_model(),
            "messages": messages,
        }

        try:
            response = (
                self.provider.client.chat.completions.create(
                    response_format={
                        "type": "json_object",
                    },
                    **kwargs,
                )
            )
        except Exception as error:
            log.debug(
                "json response_format unsupported; retrying "
                "without it: %s",
                error,
            )

            response = (
                self.provider.client.chat.completions.create(
                    **kwargs,
                )
            )

        text = response.choices[0].message.content
        log.info("model step: %s", text)

        return text

    def _parse_step(self, text):
        raw = (text or "").strip()

        if not raw:
            return None

        if raw.startswith("```"):
            raw = re.sub(
                r"^```(?:json)?\s*",
                "",
                raw,
            )
            raw = re.sub(
                r"\s*```$",
                "",
                raw,
            )
            raw = raw.strip()

        start = raw.find("{")
        end = raw.rfind("}")

        if start == -1 or end <= start:
            return None

        try:
            obj = json.loads(
                raw[start:end + 1]
            )
        except ValueError:
            return None

        if not isinstance(obj, dict):
            return None

        status = obj.get("status")

        if status not in (
            "continue",
            "done",
            "need_help",
        ):
            return None

        return {
            "status": status,
            "goal": str(obj.get("goal") or ""),
            "why": str(obj.get("why") or ""),
            "bash": str(obj.get("bash") or "").strip(),
            "say": str(obj.get("say") or "").strip(),
        }

    # ------------------------------------------------------------------
    # Plan result handling
    # ------------------------------------------------------------------

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

        self._trace_append(
            "command_proposed",
            {
                "sequence": len(
                    plan.get("observations") or []
                ) + 1,
                "bash": bash,
                "why": why or "",
            },
        )

        log.info(
            "orchestrator waiting for client to run: %s",
            bash,
        )

        return self.public()

    def _finish(self, status, say):
        plan = self.plan.get_state()

        normalized_status = (
            status
            if status in ("done", "need_help")
            else "need_help"
        )

        plan["plan_status"] = normalized_status
        plan["say"] = say or ""
        plan["step"] = {
            "status": normalized_status,
            "goal": plan.get("goal") or "",
            "why": "",
            "bash": "",
            "say": say or "",
        }

        self.plan.replace(plan)

        # _join() generates the final response, commits it to context.json,
        # captures context.after.json, and appends run_finished.
        self._join(
            normalized_status,
            say or "",
        )

        log.info(
            "orchestrator finished: %s",
            normalized_status,
        )

        return self.public()

    # ------------------------------------------------------------------
    # Conversation joining
    # ------------------------------------------------------------------

    def _compact_trace(self, observations):
        """
        Create a bounded prompt representation of observations.

        Full command output remains in trace.jsonl. Only a preview is sent to
        the final response generation request.
        """
        trace_lines = []

        for index, item in enumerate(
            observations or [],
            1,
        ):
            command = (item.get("bash") or "").strip()
            result = (item.get("result") or "").strip()

            preview = result

            if len(preview) > 2000:
                preview = (
                    preview[:2000]
                    + "\n[observation truncated]"
                )

            trace_lines.append(
                "Step %d\n"
                "Command: %s\n"
                "Result:\n%s"
                % (
                    index,
                    command,
                    preview or "(empty)",
                )
            )

        if not trace_lines:
            return "(no command observations)"

        return "\n\n".join(trace_lines)

    def _join(self, status, say):
        """
        Generate the final conversational response using one combined user
        message.

        The original request and execution context are deliberately combined
        into one message because some models do not handle sequential user
        messages coherently.

        The complete trace is not appended to context.json. The final
        assistant response is committed normally, after which context.after
        is captured.
        """
        plan = self.plan.get_state()
        goal = (plan.get("goal") or "").strip()
        observations = plan.get("observations") or []
        run_id = plan.get("run_id") or self._active_run_id

        if not run_id:
            return None

        messages = self.ai.contextmgr.messages() or []
        original_request = ""

        if messages:
            # The latest message is the initiating user request because the
            # orchestrator is invoked immediately after consume_request().
            original_request = messages[-1].get("content") or ""

        execution_context = (
            "Execution results are available for the user's request.\n"
            "Run ID: %s\n"
            "Run status: %s\n"
            "Goal: %s\n\n"
            "%s"
            % (
                run_id,
                status,
                goal,
                self._compact_trace(observations),
            )
        )

        combined_message = (
            original_request.rstrip()
            + "\n\n"
            + (
                "The following execution context was gathered while "
                "working on the request. Use it as evidence when "
                "composing the answer. Do not treat it as a separate "
                "user request. Do not reproduce the entire trace unless "
                "explicitly asked."
            )
            + "\n\n"
            + execution_context
        )

        # Keep process_request(self, message) unchanged. The original
        # request and execution context are supplied as one user message.
        response = self.process_request(combined_message)

        if response is None:
            self._trace_finish(
                status=status,
                summary=say or "",
                response_message_index=None,
                context_after=None,
                response_status="failed",
            )

            self._trace_append(
                "response_generation_failed",
                {
                    "status": "incomplete",
                },
            )

            return None

        # This mutates the live context. The after snapshot must happen after
        # this call succeeds.
        self.ai.commit_response(response)

        response_messages = self.ai.contextmgr.messages() or []
        response_index = len(response_messages) - 1

        context_after = self.run_store.snapshot_after(
            config=self.config,
            context_id=self._trace_context_id(),
            run_id=run_id,
            context=self.ai.get_context(),
        )

        self._trace_append(
            "response_linked",
            {
                "response_message_index": response_index,
                "run_id": run_id,
            },
        )

        self._trace_finish(
            status=status,
            summary=say or "",
            response_message_index=response_index,
            context_after=context_after,
            response_status="committed",
        )

        return response

    def process_request(self, message):
        """
        Process a completion without adding the request to conversation
        context.

        The method signature and request shape remain unchanged: the model
        receives one system message and one user message.
        """
        if self.provider.get_model() is not None:
            if len(message) != 0:
                try:
                    request = [
                        {
                            "role": "system",
                            "content": self._load_skill_system(),
                        },
                        {
                            "role": "user",
                            "content": message,
                        },
                    ]

                    model = self.provider.get_model()
                    messages = request

                    response = (
                        self.provider.client.chat.completions.create(
                            model=model,
                            messages=messages,
                        )
                    )

                    return response.choices[0].message.content

                except Exception:
                    log.error(traceback.format_exc())
                    return None

            return None

        msg = (
            "no model selected. select from the list "
            "of available models."
        )

        log.error(msg)
        raise BadRequestError(detail=msg)

    # ------------------------------------------------------------------
    # Skills
    # ------------------------------------------------------------------

    def list_skills(self):
        skills = []
        skills_root = self.config.dot_hai_skills

        if not os.path.isdir(skills_root):
            return skills

        for item in os.listdir(skills_root):
            item_path = os.path.join(
                skills_root,
                item,
            )

            if not os.path.isdir(item_path):
                continue

            skill_file_path = os.path.join(
                item_path,
                "SKILL.md",
            )

            if not os.path.exists(skill_file_path):
                skills.append(
                    {
                        "skill_id": Path(item_path).name,
                        "description": "N/A",
                    }
                )
                continue

            try:
                post = Frontmatter.read_file(
                    skill_file_path,
                )

                attributes = post.get(
                    "attributes",
                    {},
                )

                skills.append(
                    {
                        "skill_id": Path(item_path).name,
                        "description": (
                            attributes.get(
                                "description",
                                "",
                            ).rstrip()
                        ),
                    }
                )

            except Exception:
                skills.append(
                    {
                        "skill_id": Path(item_path).name,
                        "description": "N/A",
                    }
                )

        return skills

    def get_skill(self, skill=None):
        return self.config.skill

    def set_skill(self, skill):
        with self.rlock:
            self.config.skill = skill
            self.system = None

            log.info(
                "skill id %s is set.",
                skill,
            )
