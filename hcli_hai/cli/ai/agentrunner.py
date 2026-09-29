import logger
import threading
import traceback
import inspect
import os
import re
import openai
import json
import config as a
import bashlex
from ai import ai
from pathlib import Path
from hcli_problem_details import *

log = logger.Logger()

PLAN_KEYS = ("status", "goal", "why", "bash", "say")
STATUSES = ("continue", "done", "need_help")
WHITELIST = frozenset({
    "pwd", "ls", "echo", "grep", "curl", "cat", "head", "tail", "wc",
})
MAX_REPAIRS = 5


class AgentRunner:
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

            self.is_running = False
            self.config = a.Config()

            self._is_vibing = False
            self.initialized = True
            self.terminate = False
            self.assist_key = None

            current = os.path.dirname(inspect.getfile(lambda: None))
            self.agent_behavior = Path(os.path.join(current, "AGENT.md")).read_text(encoding="utf-8")

            self.ai = ai.AI()

    def __init_provider(self):
        with self.rlock:
            log.debug("initializing llm service provider")
            if self.config.provider == "ollama":
                self.client = openai.OpenAI(
                    base_url=self.config.ollama_service_url,
                    api_key="ollama",
                )
                log.debug(f"using ollama at {self.config.ollama_service_url}")

            elif self.config.provider == "xai":
                self.client = openai.OpenAI(
                    api_key=os.getenv("XAI_API_KEY"),
                    base_url="https://api.x.ai/v1",
                )
                log.debug("using grok (xai) at https://api.x.ai/v1")

            else:
                msg = "no provider selected. select from the list of available providers."
                log.error(msg)
                raise BadRequestError(detail=msg)

    def set_vibe(self, should_vibe):
        with self.rlock:
            self._is_vibing = should_vibe
            if should_vibe is True:
                log.info("agent runner started.")
            else:
                self.ai.contextmgr.plan.clear()
                log.info("agent runner stopped.")

    def is_vibing(self):
        with self.rlock:
            return self._is_vibing

    def pending_bash(self):
        with self.rlock:
            raw = self.ai.contextmgr.get_plan()
            if not raw:
                return None
            try:
                plan = json.loads(raw)
            except ValueError:
                return None
            if not isinstance(plan, dict):
                return None
            if plan.get("status") != "continue":
                return None
            bash = (plan.get("bash") or "").strip()
            if not bash:
                return None
            return bash

    def __system_prompt(self):
        return (
            self.agent_behavior
            + "\n\n# Live constraints (harness-enforced)\n"
            + "Allowed programs: " + ", ".join(sorted(WHITELIST)) + "\n"
            + "If the next step needs anything else, status=need_help, bash=\"\", say=the blocker.\n"
            + "Output one JSON object with keys: " + ", ".join(PLAN_KEYS) + ".\n"
            + "No markdown. No text before or after the object.\n"
        )

    def __dump(self, plan):
        return json.dumps({k: plan.get(k, "") for k in PLAN_KEYS}, ensure_ascii=False)

    def __parse_plan(self, text):
        raw = (text or "").strip()
        if not raw:
            return None, "empty output; emit one json object with keys status,goal,why,bash,say"

        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
            raw = raw.strip()

        start, end = raw.find("{"), raw.rfind("}")
        if start == -1 or end <= start:
            return None, "no json object found; emit only one object with keys status,goal,why,bash,say"

        try:
            obj = json.loads(raw[start:end + 1])
        except ValueError as e:
            return None, "invalid json: %s" % e

        if not isinstance(obj, dict):
            return None, "top-level value must be a json object, not %s" % type(obj).__name__

        extra = set(obj) - set(PLAN_KEYS)
        missing = [k for k in PLAN_KEYS if k not in obj]
        if extra or missing:
            return None, "keys must be exactly %s; missing=%s extra=%s" % (
                list(PLAN_KEYS), missing, sorted(extra)
            )

        if obj.get("status") not in STATUSES:
            return None, "status must be one of %s, got %r" % (list(STATUSES), obj.get("status"))

        for k in PLAN_KEYS:
            if not isinstance(obj.get(k), str):
                return None, "%s must be a string" % k

        status = obj["status"]
        bash = obj["bash"].strip()
        say = obj["say"].strip()
        obj["bash"] = bash
        obj["say"] = say
        obj["goal"] = obj["goal"].strip()
        obj["why"] = obj["why"].strip()

        if status == "continue":
            if not bash:
                return None, "status=continue requires a non-empty bash"
            if say:
                return None, "status=continue requires say=\"\""
        else:
            if bash:
                return None, "status=%s requires bash=\"\"" % status
            if not say:
                return None, "status=%s requires a non-empty say" % status

        return obj, None

    def __bash_error(self, command_string):
        command_string = (command_string or "").strip()
        if not command_string:
            return "status=continue requires a non-empty bash"
        if "\n" in command_string:
            return "bash must be one simple command line"

        try:
            trees = bashlex.parse(command_string)
        except Exception as e:
            return "invalid bash syntax: %s" % e

        if len(trees) != 1:
            return "bash must be exactly one command line"

        programs = []
        forbidden = []

        def walk(node):
            kind = getattr(node, "kind", None)
            if kind == "pipeline":
                forbidden.append("pipes")
            elif kind in ("commandsubstitution", "processsubstitution"):
                forbidden.append("substitution")
            elif kind == "redirect":
                forbidden.append("redirects")
            elif kind == "operator":
                forbidden.append("chaining")
            elif kind == "command":
                parts = getattr(node, "parts", None) or []
                if parts and getattr(parts[0], "kind", None) == "word":
                    programs.append(parts[0].word)
            for part in getattr(node, "parts", None) or []:
                walk(part)

        walk(trees[0])

        if forbidden:
            return "bash must be one simple command line; no pipes, chaining, redirects, or substitution"
        if len(programs) != 1:
            return "bash must invoke exactly one program, found %s" % programs
        if programs[0] not in WHITELIST:
            return (
                "unauthorized command %r; allowed: %s. "
                "use status=need_help if the task requires it"
                % (programs[0], ", ".join(sorted(WHITELIST)))
            )
        return None

    def __step(self, messages, observation=None):
        with self.lock:
            self.is_running = True
            self.terminate = False
            try:
                if not self.is_vibing():
                    return None

                log.info("engaging harness.")
                pending = self.pending_bash()

                if observation is None:
                    if pending:
                        log.info("agent waiting for observation via hai agent next")
                        return None
                    user = messages[-1]["content"]
                else:
                    if not pending:
                        log.warning("no pending bash step to observe; refusing next.")
                        return None
                    plan = self.ai.contextmgr.get_plan() or ""
                    self.ai.contextmgr.append_observation(pending, observation)
                    user = self.__scratch(plan)

                last = None
                critique = None
                partial_goal = ""

                for attempt in range(MAX_REPAIRS):
                    if critique is None:
                        prompt = user
                    else:
                        log.warning("plan rejected (%d/%d): %s" % (attempt, MAX_REPAIRS, critique))
                        prev = last if last and len(last) <= 800 else (last[:800] + "\n...")
                        prompt = (
                            user
                            + "\n\nYour previous output was rejected. It is not the user task.\n"
                            + "Do not describe JSON. Do not invent keys. Do not process the previous output.\n"
                            + "Stay on the original goal. If observations already answer it, status=done, bash=\"\", put the answer in say.\n"
                            + "error: %s\n" % critique
                            + "rejected output (do not copy its keys):\n%s\n\n" % prev
                            + "Emit exactly this shape:\n"
                            + '{"status":"done","goal":"<original goal>","why":"","bash":"","say":"<answer>"}\n'
                            + "or status=continue with a whitelisted bash and say=\"\".\n"
                        )

                    last = self.__complete([
                        {"role": "system", "content": self.__system_prompt()},
                        {"role": "user", "content": prompt},
                    ])
                    if last is None:
                        return None

                    plan, critique = self.__parse_plan(last)
                    if plan is not None and plan.get("goal"):
                        partial_goal = plan["goal"]
                    if plan is None:
                        continue

                    if plan["status"] == "continue":
                        critique = self.__bash_error(plan["bash"])
                        if critique:
                            continue

                    payload = self.__dump(plan)
                    self.ai.contextmgr.set_plan(payload)
                    self.__join_if_terminal(payload)
                    return payload

                log.error("plan repair exhausted: %s" % (critique or "unknown"))
                fallback = {
                    "status": "need_help",
                    "goal": partial_goal,
                    "why": "",
                    "bash": "",
                    "say": "could not produce a valid plan: %s" % (critique or "unknown"),
                }
                payload = self.__dump(fallback)
                self.ai.contextmgr.set_plan(payload)
                self.__join_if_terminal(payload)
                return payload

            except TerminationException:
                log.debug("agent terminated.")
            except Exception:
                log.error(traceback.format_exc())
            finally:
                self.terminate = False
                self.is_running = False
                log.info("disengaging harness.")
            return None

    def harness(self):
        return self.__step(self.ai.contextmgr.messages())

    def next(self, observation):
        return self.__step(self.ai.contextmgr.messages(), observation)

    def __scratch(self, current_plan):
        parts = []
        if current_plan:
            parts.append("current plan:\n" + current_plan)

        obs = self.ai.contextmgr.observations()
        if obs:
            blob = []
            for i, item in enumerate(obs, 1):
                blob.append(
                    "observation %d\nbash: %s\nresult:\n%s"
                    % (i, item.get("bash", ""), item.get("result", ""))
                )
            parts.append("\n\n".join(blob))

        parts.append(
            "The blocks above are evidence, not a format to copy.\n"
            "Emit one plan object with keys status,goal,why,bash,say.\n"
            "If the observations already answer the goal, status=done, bash=\"\", say=the answer.\n"
            "Do not invent keys. Do not dump observations back as JSON.\n"
            "Do not assume output you have not been given."
        )
        return "\n\n".join(parts)

    def __complete(self, messages):
        model = self.config.model
        if self.config.provider is None or model is None:
            log.warning("no provider or model selected. select from the list of available providers and models.")
            return None

        self.__init_provider()
        kwargs = dict(model=model, messages=messages)
        response = None
        try:
            response = self.client.chat.completions.create(
                response_format={"type": "json_object"},
                **kwargs
            )
        except Exception as e:
            log.debug("json response_format unsupported; retrying without it: %s" % e)
            response = self.client.chat.completions.create(**kwargs)

        log.debug(response)
        text = response.choices[0].message.content
        log.info(text)
        return text

    def __join_if_terminal(self, response):
        try:
            plan = json.loads(response)
        except ValueError:
            return

        if not isinstance(plan, dict):
            return

        status = plan.get("status")
        bash = (plan.get("bash") or "").strip()
        say = (plan.get("say") or "").strip()

        terminal = status in ("done", "need_help") or (not bash and bool(say))
        if not terminal or not say:
            return

        self.ai.commit_response(say)
        self.ai.contextmgr.plan.clear()

    def check_termination(self):
        if self.terminate:
            raise TerminationException("terminated")


class TerminationException(Exception):
    pass
