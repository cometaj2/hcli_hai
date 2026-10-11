import os
import openai
import logger
import config as c
import threading
from hcli_problem_details import *

log = logger.Logger()


class Provider:
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
            self.initialized = True
            self.config = c.Config()
            if self.config.provider is not None:
                self.__init_provider()

    def __init_provider(self):
        with self.rlock:
            log.debug("Initializing LLM service provider.")
            if self.config.provider == "ollama":
                self.client = openai.OpenAI(
                    base_url=self.config.ollama_service_url,
                    api_key="ollama",   # Ollama ignores the key
                )
                log.debug(f"using ollama at {self.config.ollama_service_url}")

            elif self.config.provider == "openai":
                self.client = openai.OpenAI(
                    base_url=self.config.openai_service_url,
                    api_key=os.getenv("OPENAI_API_KEY"),
                )
                log.debug("using OpenAI at " + self.config.openai_service_url)

            elif self.config.provider == "xai":
                self.client = openai.OpenAI(
                    base_url=self.config.xai_service_url,
                    api_key=os.getenv("XAI_API_KEY"),
                )
                log.debug("using grok (xai) at " + self.config.xai_service_url)

            else:
                msg = "no provider selected. select from the list of available providers."
                log.error(msg)
                raise BadRequestError(detail=msg)

    # get the current LLM service provider to use
    def get_provider(self):
        return self.config.provider

    # list available models
    def list_models(self):
        try:
            # OpenAI client data structure in support of both ollama and xai
            installed_models = self.client.models.list()
            models = {}

            for model in installed_models.data:
                models[model.id] = {}

            return models
        except Exception as e:
            log.error(traceback.format_exc())
            msg = f"unable to list models. is the backend misconfigured?"
            log.error(msg)
            raise InternalServerError(detail=msg)

    # get the currently selected model
    def get_model(self):
        return self.config.model

    # set the model to use
    def set_model(self, model):
        with self.rlock:
            models = self.list_models()
            if model in models:
                self.config.model = model
                msg = f"{self.config.model} model selected."
                log.info(msg)
            else:
                msg = "invalid model selected. select from the list of available models."
                log.error(msg)
                raise BadRequestError(detail=msg)

    # list available providers
    def list_providers(self):
        try:
            return self.config.providers
        except Exception as e:
            log.error(traceback.format_exc())
            msg = f"unable to list LLM service providers. Is the configuration broken?"
            log.error(msg)
            raise InternalServerError(detail=msg)

    # set the provider to use
    def set_provider(self, provider):
        with self.rlock:
            providers = self.list_providers()
            if provider in providers:
                if self.config.provider != provider:
                    self.config.model = None
                self.config.provider = provider

                if self.config.provider is not None:
                    self.__init_provider()

                msg = f"{self.config.provider} provider selected."
                log.info(msg)
            else:
                msg = "invalid provider selected. select from the list of available providers."
                log.error(msg)
                raise BadRequestError(detail=msg)
