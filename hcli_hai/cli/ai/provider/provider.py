import os
import threading
import traceback

import openai
import logger
import config as c

from hcli_problem_details import *

log = logger.Logger()


class GuardedCompletions:
    def __init__(self, provider, completions):
        self.provider = provider
        self.completions = completions

    def create(self, *args, **kwargs):
        model = self.provider.require_model()

        requested_model = kwargs.get("model")
        if requested_model and requested_model != model:
            msg="model " + requested_model + " is not the selected model. selected model: " + model + "."
            log.error(msg)
            raise BadRequestError(detail=msg)

        kwargs["model"] = model
        return self.completions.create(*args, **kwargs)


class GuardedChat:
    def __init__(self, provider, chat):
        self.completions = GuardedCompletions(
            provider,
            chat.completions,
        )


class GuardedClient:
    def __init__(self, provider, client):
        self._client = client
        self.chat = GuardedChat(provider, client.chat)
        self.models = client.models

    def __getattr__(self, name):
        return getattr(self._client, name)


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
            self.config = c.Config()
            self.initialized = True

            if self.config.provider is not None:
                self.__init_provider()

    def __init_provider(self):
        with self.rlock:
            log.debug("Initializing LLM service provider.")

            if self.config.provider == "ollama":
                client = openai.OpenAI(
                    base_url=self.config.ollama_service_url,
                    api_key="ollama",
                )
                log.debug("using ollama at " + self.config.ollama_service_url)

            elif self.config.provider == "openai":
                client = openai.OpenAI(
                    base_url=self.config.openai_service_url,
                    api_key=os.getenv("OPENAI_API_KEY"),
                )
                log.debug("using OpenAI at " + self.config.openai_service_url)

            elif self.config.provider == "xai":
                client = openai.OpenAI(
                    base_url=self.config.xai_service_url,
                    api_key=os.getenv("XAI_API_KEY"),
                )
                log.debug("using grok (xai) at " + self.config.xai_service_url)

            else:
                msg="no provider selected. select from the list of available providers."
                log.error(msg)
                raise BadRequestError(detail=msg)

            self.client = GuardedClient(self, client)

    def get_provider(self):
        return self.config.provider

    def list_models(self):
        try:
            installed_models = self.client.models.list()
            models = {}

            for model in installed_models.data:
                models[model.id] = {}

            return models

        except Exception:
            log.error(traceback.format_exc())
            msg = "unable to list models. is the backend misconfigured?"
            log.error(msg)
            raise InternalServerError(detail=msg)

    def get_model(self):
        return self.config.model

    def require_model(self):
        with self.rlock:
            if not self.config.provider:
                msg="Select a provider before generating content."
                log.error(msg)
                raise BadRequestError(detail=msg)

            model = self.config.model

            if not model:
                msg = "no model is selected for provider " + self.config.provider + ". select a model first."
                log.error(msg)
                raise BadRequestError(detail=msg)

            return model

    def set_model(self, model):
        with self.rlock:
            if not self.config.provider:
                msg="Select a provider before selecting a model."
                log.error(msg)
                raise BadRequestError(detail=msg)

            models = self.list_models()

            if model not in models:
                msg="invalid model selected. select from the list of available models."
                log.error(msg)
                raise BadRequestError(detail=msg)

            self.config.model = model
            msg = f"{self.config.model} model selected."
            log.info(msg)

    def list_providers(self):
        try:
            return self.config.providers

        except Exception:
            msg="unable to list LLM service providers. Is the configuration broken?"
            log.error(msg)
            raise InternalServerError(detail=msg)

    def set_provider(self, provider):
        with self.rlock:
            providers = self.list_providers()

            if provider not in providers:
                msg="invalid provider selected. select from the list of available providers."
                log.error(msg)
                raise BadRequestError(detail=msg)

            previous_provider = self.config.provider
            previous_model = self.config.model

            try:
                self.config.provider = provider
                self.config.model = None
                self.__init_provider()

            except Exception as e:
                self.config.provider = previous_provider
                self.config.model = previous_model
                raise e

            msg = f"{self.config.provider} provider selected."
            log.info(msg)
