import io
import logger
import threading
import traceback
import time
import re
import os
import config as a
import openai
import numpy as np
import sounddevice as sd
from hcli_problem_details import *
from piper.voice import PiperVoice

from ai import voice as v

log = logger.Logger()


# Singleton AssistantRunner
class AssistantRunner:
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

            self.model_path = self.config.assistant_tts_path

            self._is_assisting = False
            self.initialized = True
            self.terminate = False

            self.voice = v.Voice(self.config.assistant_tts_path,
                                 check_termination = self.check_termination)

    def __init_provider(self):
        with self.rlock:
            log.info("initializing llm service provider")
            if self.config.provider == "ollama":
                self.client = openai.OpenAI(
                    base_url=self.config.ollama_service_url,
                    api_key="ollama",   # Ollama ignores the key
                )
                log.info(f"using ollama at {self.config.ollama_service_url}")

            elif self.config.provider == "xai":
                self.client = openai.OpenAI(
                    api_key=os.getenv("XAI_API_KEY"),
                    base_url="https://api.x.ai/v1",
                )
                log.info("using grok (xai) at https://api.x.ai/v1")

            else:
                msg = "no provider selected. select from the list of available providers."
                log.error(msg)
                raise BadRequestError(detail=msg)

    def set_assist(self, should_assist):
        if should_assist == False:
            self.terminate = True
            self.voice.stop()
        with self.rlock:
            self._is_assisting = should_assist
            if should_assist is True:
                log.info(f"[ hai ] assistant runner started.")
            else:
                self.terminate = True
                log.info(f"[ hai ] assistant runner stopped.")

    def is_assisting(self):
        with self.rlock:
            return self._is_assisting

    def speak(self, message):
        if self.terminate:
            return
        self.voice.speak(message)
#         with self.rlock:
#             log.info("[ hai ] " + message)
# 
#             if self.voice is None and self.model_path is not None and self.model_path != "":
#                 self.voice = PiperVoice.load(self.model_path)
#                 self.sample_rate = self.voice.config.sample_rate
# 
#             stream = sd.RawOutputStream(
#                 samplerate=self.sample_rate,
#                 channels=1,
#                 dtype='int16'
#             )
#             stream.start()
# 
#             try:
#                 for chunk in self.voice.synthesize(message):
#                     audio_chunk = np.frombuffer(chunk.audio_int16_bytes, dtype=np.int16)
#                     stream.write(audio_chunk)
#                     self.check_termination()
#             finally:
#                 stream.stop()
#                 stream.close()

    def run(self, messages):
        self.is_running = True
        self.terminate = False

        try:
            log.info("[ hai ] attempting to assist...")

            q_content = messages[-2]['content']
            a_content = messages[-1]['content']

            assistance = [{"role": "system", "content": self.config.assistant_behavior}]
            question = { "role" : "user", "content" : "user: " + q_content + "\n\nassistant: " + a_content }
            assistance.append(question)

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
                    msg = "[ hai ] no provider or model selected. select from the list of available providers and models."
                    log.warning(msg)

            except Exception as e:
                log.error(traceback.format_exc())
                return None

            if (response is not None):
                self.voice.speak(response.choices[0].message.content)
#                 output_response = response.choices[0].message.content
#                 self.speak(output_response)

        except TerminationException as e:
            log.error(traceback.format_exc())
            self.abort()
        except Exception as e:
            log.error(traceback.format_exc())
            self.abort()
        finally:
            self.terminate = False
            self.is_running = False

        log.info("[ hai ] done assisting...")

        return

    def check_termination(self):
        if self.terminate:
            raise TerminationException("[ hai ] terminated")

    def abort(self):
        self.is_running = False
        self.terminate = False
        self.voice.stop()

class TerminationException(Exception):
    pass
