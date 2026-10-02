import json
import io
import os
import sys
import re
import inspect
import traceback
import shutil
import time
import threading

import config as a
import logger
from ai import context as c
from hcli_problem_details import *

from datetime import datetime
import openai

log = logger.Logger()


class AI:
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
            log.debug("Initializing AI singleton.")
            self.rlock = threading.RLock()
            self.config = None
            self.contextmgr = None
            self.client = None
            self.config = a.Config()
            self.contextmgr = c.ContextManager()
            if self.config.provider is not None:
                self.__init_provider()
            self.initialized = True

            log.debug(f"AI initialization complete: config={bool(self.config)}, contextmgr={bool(self.contextmgr)}")

    def __init_provider(self):
        with self.rlock:
            log.debug("Initializing LLM service provider.")
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

    # Consume the user prompt request into the context
    def consume_request(self, text):
        with self.rlock:
            if self.config.model is not None:
                if text != "":
                    text = text.rstrip()
                    question = { "role" : "user", "content" : text }
                    self.contextmgr.append(question)
                    self.contextmgr.trim()

                    tokens = self.contextmgr.counter.get_stats(self.contextmgr.context)
                    log.info("request  - total context tokens: " + str(tokens['total_tokens']))

                    return text

    # Process the completion to return a response without adding to the context.
    def process_request(self):
        with self.rlock:
            if self.config.model is not None:
                if self.contextmgr.counter.total_tokens != 0:
                    try:
                        model = self.config.model

                        messages = [msg for msg in self.contextmgr.messages()]
                        response = self.client.chat.completions.create(
                                                        model=model,
                                                        messages=messages
                                                   )
                        return response.choices[0].message.content
                    except Exception as e:
                        log.error(traceback.format_exc())
                        return None
                else:
                    return None

            else:
                msg = "no model selected. select from the list of available models."
                log.error(msg)
                raise BadRequestError(detail=msg)

    # add an additional response to the chat context.
    def commit_response(self, text):
        with self.rlock:
            self.contextmgr.append({ "role" : "assistant", "content" : text})

            tokens = self.contextmgr.counter.get_stats(self.contextmgr.context)
            log.info("response - total context tokens: " + str(tokens['total_tokens']))

            # Auto-title after first assistant response if no title exists yet

            if not self.contextmgr.title():
                title = self.generate_title()
                self.set_title(title)
            self.contextmgr.save()

    # get the current context as json output
    def get_context(self):
        return self.contextmgr.get_context()

    # get the current context as text output
    def get_readable_context(self):
        return self.contextmgr.get_readable_context()

    # reset the current context (clean slate)
    def reset(self):
        with self.rlock:
            self.contextmgr.reset()
            self.config.context
            log.warning(f"The {self.config.context} context has been reset.")

    # set the persistent context behavior (system prompt)
    def behavior(self, inputstream):
        with self.rlock:
            self.contextmgr.behavior(inputstream)

    # list all contexts
    def ls(self):
        with self.rlock:
            contexts = []
            share = self.config.dot_hai_context

            # Iterate through all items in the folder
            for item in os.listdir(share):
                item_path = os.path.join(share, item)
                # Check if the item is a directory
                if os.path.isdir(item_path):

                    context_file = os.path.join(item_path, 'context.json')
                    if os.path.exists(context_file):
                        try:
                            with open(context_file, 'r') as f:
                                context = c.Context(json.load(f))
                                title = context.title
                                name = context.name
                                update_time = os.path.getmtime(context_file)
                                contexts.append({
                                    'context_id': item,
                                    'title': title,
                                    'name': name,
                                    'update_time': update_time
                                })
                        except json.JSONDecodeError:
                            # If there's an error reading the JSON, just use the directory name
                            contexts.append({
                                'context_id': item,
                                'title': 'N/A',
                                'name': 'N/A',
                                'update_time': os.path.getmtime(context_file) if os.path.exists(context_file) else 0
                            })
                    else:
                        # If context.json doesn't exist, use the directory name only
                        contexts.append({
                            'context_id': item,
                            'title': 'N/A',
                            'name': 'N/A',
                            'update_time': 0
                        })

            # Sort contexts by creation time, newest at the bottom of the list
            sorted_contexts = sorted(contexts, key=lambda x: x['update_time'], reverse=False)

            # Format the creation time and remove it from the final output
            for context in sorted_contexts:
                context['update_time'] = datetime.fromtimestamp(context['update_time']).strftime('%Y-%m-%d %H:%M:%S')

            return sorted_contexts

    # set the current context
    def set(self, context_id):
        with self.rlock:
            contexts = self.ls()
            context_ids = [context['context_id'] for context in contexts]
            if context_id in context_ids:
                self.contextmgr.set(context_id)
                log.info(f"context id {context_id} is set.")
            else:
                msg = f"provided context id {context_id} was not found in available contexts."
                log.error(msg)
                raise NotFoundError(detail=msg)

    # create a new context
    def new(self):
        with self.rlock:
            if not os.path.exists(self.config.dot_hai_config_file):
                self.config.init()
            else:
                self.config.context = str(self.config.generate_id())
                self.config.save()
            self.contextmgr.init()
            return self.current()

    # delete the context_id context
    def rm(self, context_id):
        with self.rlock:
            context_folder = os.path.join(self.config.dot_hai_context, context_id)
            if os.path.exists(context_folder):
                shutil.rmtree(context_folder)
                log.info("Removed " + context_folder)

    # get the current context ID
    def current(self):
        with self.rlock:
            return self.config.context

    # list available models
    def list_models(self):
        with self.rlock:
            try:
                # OpenAI client data structure in support of both ollama and xai
                installed_models = self.client.models.list()
                models = {}

                for model in installed_models.data:
                    models[model.id] = {}

                return models
            except Exception as e:
                log.error(traceback.format_exc())
                msg = f"unable to list ollama models. is the ollama backend misconfigured ({self.config.ollama_service_url})?"
                log.error(msg)
                raise InternalServerError(detail=msg)

    # get the model to use
    def model(self):
        with self.rlock:
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
        with self.rlock:
            try:
                return self.config.providers
            except Exception as e:
                log.error(traceback.format_exc())
                msg = f"unable to list LLM service providers. Is the configuration broken?"
                log.error(msg)
                raise InternalServerError(detail=msg)

    # get the current LLM service provider to use
    def provider(self):
        with self.rlock:
            return self.config.provider

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

    def generate_title(self, max_messages: int = 4):
        """
        Generate a concise title (<=10 words) using only the most recent messages.
        System messages are explicitly excluded so they don't interfere with the
        title generation system prompt.
        The title request/response is never added to the conversation context.
        """
        with self.rlock:
            if self.config.model is None:
                msg = "no model selected. select from the list of available models."
                log.error(msg)
                raise BadRequestError(detail=msg)

            all_messages = self.contextmgr.messages()
            if not all_messages:
                return None

            # Take only the last N messages
            recent_messages = all_messages[-max_messages:]

            # Explicitly remove any system messages to avoid interference
            recent_messages = [
                msg for msg in recent_messages if msg.get("role") != "system"
            ]

            if not recent_messages:
                return None

            title_prompt = "You are a title generator. Create a short, complete, and self-contained title that summarizes the main topic or goal of the conversation. The title must be a well-formed phrase or headline. Do not use sentence fragments, open-ended questions, or incomplete thoughts. Output about 10 words. Return only the title, nothing else."

            title_messages = [*recent_messages,
                              { "role": "user", "content": title_prompt}
                             ]

            try:
                response = self.client.chat.completions.create(
                    model=self.config.model,
                    messages=title_messages,
                    max_tokens=20,
                    temperature=0.2,
                )

                raw_title = response.choices[0].message.content.strip()
                title = " ".join(raw_title.strip('"').split()[:10])

                log.info(f"Generated title: {title}")
                return title

            except Exception as e:
                log.error(traceback.format_exc())
                return None

    # get the context name
    def name(self):
        with self.rlock:
            return self.contextmgr.name()

    # set the context name
    def set_name(self, name):
        with self.rlock:
            self.contextmgr.set_name(name)

    # get the context title
    def title(self):
        with self.rlock:
            return self.contextmgr.title()

    # set the context title
    def set_title(self, title):
        with self.rlock:
            if title is not None:
                self.contextmgr.set_title(title)

    # output current plan
    def task(self):
        with self.rlock:
            return self.contextmgr.get_step()
