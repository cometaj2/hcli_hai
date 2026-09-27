import logger
import threading
import traceback
import time
import inspect
import re
import os
import openai
import json
import config as a
from ai import ai
from ai.router import froute
from pathlib import Path

log = logger.Logger()


# Singleton AgentRunner
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
                    api_key="ollama",   # Ollama ignores the key
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
                log.info(f"agent runner started.")
            else:
                log.info(f"agent runner stopped.")

    def is_vibing(self):
        with self.rlock:
            return self._is_vibing

    def harness(self, text, messages):
        self.is_running = True
        self.terminate = False

        try:
            decision = froute(text)
            if decision == "do":
                self.__run(messages)
            else:
                return None
        except TerminationException as e:
            self.abort()
        except Exception as e:
            self.abort()
        finally:
            self.terminate = False
            self.is_running = False

        return

    def __run(self, messages):
        self.is_running = True
        self.terminate = False

        try:
            log.info("engaging harness.")

            q_content = messages[-2]['content']
            a_content = messages[-1]['content']

            assistance = [{"role": "system", "content": self.agent_behavior}]

            question = { "role" : "user", "content" : a_content }
            assistance.append(question)

            print(assistance)

            response = None
            try:
                model = self.config.model

                if self.config.provider is not None and self.config.model is not None:
                    self.__init_provider()

                    response = self.client.chat.completions.create(
                                                    model=model,
                                                    messages=assistance
                                               )
                    log.debug(response)
                else:
                    msg = "no provider or model selected. select from the list of available providers and models."
                    log.warning(msg)

            except Exception as e:
                log.error(traceback.format_exc())
                return None

            if (response is not None):
                response = response.choices[0].message.content
                log.info(response)
                if self.__is_valid_json(response):
                    self.ai.contextmgr.set_status(response)
                else:
                    log.error("invalid json task")
                    raise TerminationException("terminated")

            return response

        except TerminationException as e:
            log.debug("agent terminated.")
            self.abort()
        except Exception as e:
            log.error(traceback.format_exc())
            self.abort()
        finally:
            self.terminate = False
            self.is_running = False

        log.info("disengaging harness.")

        return

    def check_termination(self):
        if self.terminate:
            raise TerminationException("terminated")

    def abort(self):
        self.is_running = False
        self.terminate = False

class TerminationException(Exception):
    pass
