import logger
import threading
import traceback
import inspect
import os
import openai
import json
import config as a
from ai import ai
from ai.router import froute
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
            raw = self.ai.contextmgr.status()
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

    def harness(self, text, messages):
        self.is_running = True
        self.terminate = False

        try:
            if not self.is_vibing():
                return None

            if self.pending_bash():
                log.info("agent waiting for observation via hai agent next")
                return None

            decision = froute(text)
            if decision != "do":
                return None

            return self.__run(messages)

        except TerminationException:
            self.abort()
        except Exception:
            log.error(traceback.format_exc())
            self.abort()
        finally:
            self.terminate = False
            self.is_running = False

        return None

    def __run(self, messages):
        self.is_running = True
        self.terminate = False

        try:
            log.info("engaging harness.")

            a_content = messages[-1]["content"]
            assistance = [
                {"role": "system", "content": self.agent_behavior},
                {"role": "user", "content": a_content},
            ]

            response = self.__complete(assistance)
            if response is None:
                return None

            if not self.__is_valid_json(response):
                log.error("invalid json task")
                raise TerminationException("terminated")

            self.ai.contextmgr.set_status(response)
            self.__join_if_terminal(response)
            return response

        except TerminationException:
            log.debug("agent terminated.")
            self.abort()
        except Exception:
            log.error(traceback.format_exc())
            self.abort()
        finally:
            self.terminate = False
            self.is_running = False
            log.info("disengaging harness.")

        return None

    def next(self, observation):
        self.is_running = True
        self.terminate = False

        try:
            if not self.is_vibing():
                return None

            log.info("engaging harness next.")

            bash = self.pending_bash()
            if not bash:
                log.warning("no pending bash step to observe; refusing next.")
                return None

            plan = self.ai.contextmgr.status() or ""
            self.ai.contextmgr.append_observation(bash, observation)

            assistance = [
                {"role": "system", "content": self.agent_behavior},
                {"role": "user", "content": self.__scratch(plan)},
            ]

            response = self.__complete(assistance)
            if response is None:
                return None

            if not self.__is_valid_json(response):
                log.error("invalid json task")
                raise TerminationException("terminated")

            self.ai.contextmgr.set_status(response)
            self.__join_if_terminal(response)
            log.info("disengaging harness.")

            return response

        except TerminationException:
            log.debug("agent terminated.")
            self.abort()
        except Exception:
            log.error(traceback.format_exc())
            self.abort()
        finally:
            self.terminate = False
            self.is_running = False

        return None

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

    def abort(self):
        self.is_running = False
        self.terminate = False


class TerminationException(Exception):
    pass
