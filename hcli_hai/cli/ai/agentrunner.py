import logger
import threading
import traceback
import inspect
import os
import openai
import json
import config as a
import bashlex
from ai import ai
from pathlib import Path
from hcli_problem_details import *

log = logger.Logger()


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

    def __is_valid_json(self, string):
        try:
            json.loads(string)
            return True
        except ValueError:
            return False

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

    def __step(self, messages, observation=None):
        with self.lock:
            self.is_running = True
            self.terminate = False
            try:
                if not self.is_vibing():
                    return None

                pending = self.pending_bash()

                if observation is None:
                    if pending:
                        log.info("agent waiting for observation via hai agent next")
                        return None
                    log.info("engaging harness.")
                    user = messages[-1]["content"]
                else:
                    if not pending:
                        log.warning("no pending bash step to observe; refusing next.")
                        return None
                    log.info("engaging harness next.")
                    plan = self.ai.contextmgr.get_plan() or ""
                    self.ai.contextmgr.append_observation(pending, observation)
                    user = self.__scratch(plan)

                response = self.__complete([
                    {"role": "system", "content": self.agent_behavior},
                    {"role": "user", "content": user},
                ])
                if response is None:
                    return None

                self.validate_plan(response)
                self.ai.contextmgr.set_plan(response)
                self.__join_if_terminal(response)
                return response

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
            "Use only these observations. Emit the next JSON object. "
            "Do not assume output you have not been given."
        )
        return "\n\n".join(parts)

    def __complete(self, messages):
        model = self.config.model
        if self.config.provider is None or model is None:
            log.warning("no provider or model selected. select from the list of available providers and models.")
            return None

        self.__init_provider()
        response = self.client.chat.completions.create(
            model=model,
            messages=messages,
        )
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

    def validate_plan(self, plan):
        if not self.__is_valid_json(plan):
            log.error("invalid json plan")
            raise TerminationException("terminated")

        task = json.loads(plan)
        if not isinstance(task, dict):
            log.error("plan is not a json object")
            raise TerminationException("terminated")

        bash = task.get("bash")
        if bash is None or not str(bash).strip():
            log.info("no bash in plan; skipping command whitelist")
            return

        bash = str(bash).strip()
        whitelist = {"echo", "ls", "grep", "curl"}
        log.info("whitelist: " + str(whitelist))
        log.info("proposed command: " + bash)
        if not self.validate_bash_command(bash, whitelist):
            raise TerminationException("terminated")

    def validate_bash_command(self, command_string, whitelist):
        try:
            # Parse the string into a Bash AST
            trees = bashlex.parse(command_string)
        except bashlex.errors.ParsingError:
            return False  # Invalid Bash syntax

        def check_node(node):
            # If the node represents an executed command
            if node.kind == 'command':
                # Extract the base command name (e.g., 'git' from 'git commit')
                parts = node.parts
                if parts and parts[0].kind == 'word':
                    command_name = parts[0].word
                    if command_name not in whitelist:
                        log.error(f"unauthorized command detected: {command_name}")
                        raise ValueError(f"unauthorized command detected: {command_name}")

            # Recursively check sub-commands (like inside pipes or subshells)
            if hasattr(node, 'parts'):
                for part in node.parts:
                    check_node(part)

        try:
            for tree in trees:
                check_node(tree)
            return True
        except ValueError:
            return False


class TerminationException(Exception):
    pass
