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
from piper.voice import PiperVoice

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
            self.exception_event = threading.Event()
            self.terminate = False
            self.is_running = False
            self.config = a.Config()

            self.model_path = self.config.assistant_tts_path
            self.voice = PiperVoice.load(self.model_path)
            self.sample_rate = self.voice.config.sample_rate

            self._is_assisting = False
            self.initialized = True

    def __init_provider(self):
        with self.rlock:
            log.debug("Initializing LLM service provider")
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

    def set_assist(self, should_assist):
        with self.rlock:
            self._is_assisting = should_assist
            if should_assist is True:
                log.info(f"[ hai ] Assistant runner started.")
            else:
                log.info(f"[ hai ] Assistant runner stopped.")

    def is_assisting(self):
        with self.rlock:
            return self._is_assisting

    def run(self, message):
        self.is_running = True
        self.terminate = False

        try:
            log.info("[ hai ] Attempting to assist...")

            content = message['content']

            assistance = [{"role": "system", "content": self.config.assistant_behavior}]
            question = { "role" : "user", "content" : content }
            assistance.append(question)

            response = None
            try:
                model = self.config.model

                if self.config.provider is not None:
                    self.__init_provider()

                    response = self.client.chat.completions.create(
                                                    model=model,
                                                    messages=assistance
                                               )
                    log.debug(response)

            except Exception as e:
                log.error(traceback.format_exc())
                return None

            if (response is not None):
                output_response = response.choices[0].message.content
                output_response_role = response.choices[0].message.role

                log.info("[ hai ] " + output_response)

                stream = sd.RawOutputStream(
                    samplerate=self.sample_rate,
                    channels=1,
                    dtype='int16'
                )
                stream.start()

                try:
                    for chunk in self.voice.synthesize(output_response):
                        audio_chunk = np.frombuffer(chunk.audio_int16_bytes, dtype=np.int16)
                        stream.write(audio_chunk)
                finally:
                    stream.stop()
                    stream.close()

        except TerminationException as e:
            log.error(traceback.format_exc())
            self.abort()
        except Exception as e:
            log.error(traceback.format_exc())
            self.abort()
        finally:
            self.terminate = False
            self.is_running = False

        log.info("[ hai ] Done assisting...")

        return

    def check_termination(self):
        if self.terminate:
            raise TerminationException("[ hai ] terminated")

    def abort(self):
        self.is_running = False
        self.terminate = False

class TerminationException(Exception):
    pass


