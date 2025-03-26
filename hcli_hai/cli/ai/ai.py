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

import config
import logger
from . import context as c
from .model import models
from hcli_problem_details import *

from datetime import datetime
from anthropic import Anthropic


logging = logger.Logger()


class AI:
    instance = None
    init_lock = threading.RLock()
    config = None
    contextmgr = None

    def __new__(cls):
        with cls.init_lock:
            if cls.instance is None:
                cls.instance = super().__new__(cls)
                cls.instance.__init_singleton()
            return cls.instance

    def __init_singleton(self):
        self.rlock = threading.RLock()
        logging.debug("Initializing AI singleton")
        self.config = config.Config()
        self.contextmgr = c.ContextManager()
        logging.debug(f"AI initialization complete: config={bool(self.config)}, contextmgr={bool(self.contextmgr)}")

    # add an additional message to the chat context and request a response for it with the LLM.
    def chat(self, inputstream):
        with self.rlock:
            if self.config.model is not None:
                inputstream = inputstream.read().decode('utf-8')
                if inputstream != "":
                    inputstream = inputstream.rstrip()
                    question = { "role" : "user", "content" : inputstream }
                    self.contextmgr.append(question)
                    self.contextmgr.trim()

                    tokens = self.contextmgr.counter.get_stats(self.contextmgr.context)
                    logging.info("Request  - total context tokens: " + str(tokens['total_tokens']))

                    if self.contextmgr.counter.total_tokens != 0:
                        try:
                            client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

                            # Separate system message from user messages
                            model = models[self.config.model]
                            system_message = next((msg["content"] for msg in self.contextmgr.messages() if msg["role"] == "system"), "")
                            user_messages = [msg for msg in self.contextmgr.messages() if msg["role"] != "system"]

                            response = client.messages.create(
                                                                 **model,
                                                                 system=system_message,
                                                                 messages=user_messages
                                                             )

                            logging.debug(response)

                        except Exception as e:
                            logging.error(traceback.format_exc())
                            return None
                    else:
                        msg = "the token trim backoff completely collapsed. this means that the stream was too large to fit within the total allowable context limit of " + str(self.contextmgr.counter.max_context_length) + " tokens, and the last trimming operation ended up completely wiping out the remaining conversation context."
                        logging.error(msg)
                        self.contextmgr.save()
                        PayloadTooLargeError(detail="hai: " + msg)

                        return warning

                    output_response = response

                    # Extract the text content from the response
                    output_content = " ".join([block.text for block in output_response.content if block.type == 'text'])

                    self.contextmgr.append({ "role" : output_response.role, "content" : output_content})

                    tokens = self.contextmgr.counter.get_stats(self.contextmgr.context)
                    logging.info("Response - total context tokens: " + str(tokens['total_tokens']))

                    output = output_content

                    self.contextmgr.generate_title()
                    self.contextmgr.save()

                    return output
            else:
                msg = "no model selected. select one from the list of models."
                logging.error(msg)
                raise BadRequestError(detail="hai: " + msg)

    # get the current context as json output
    def get_context(self):
        with self.rlock:
            return self.contextmgr.get_context()

    # get the current context as text output
    def get_readable_context(self):
        with self.rlock:
            return self.contextmgr.get_readable_context()

    # clear the current context (clean slate)
    def clear(self):
        with self.rlock:
            self.contextmgr.clear()

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
            else:
                msg = f"provided context id {context_id} was not found in available contexts."
                logging.error(msg)
                raise NotFoundError(detail="hai: " + msg)

    # create a new context
    def new(self):
        with self.rlock:
            if os.path.exists(self.config.dot_hai_config_file):
                os.remove(self.config.dot_hai_config_file)
            self.config.init()
            self.contextmgr.init()

            return self.current()

    # delete the current context
    def rm(self, context_id):
        with self.rlock:
            context_folder = os.path.join(self.config.dot_hai_context, context_id)
            if os.path.exists(context_folder):
                shutil.rmtree(context_folder)
                logging.info("Removed " + context_folder)

    # get the current context ID
    def current(self):
        with self.rlock:
            return self.config.context

    # list available models
    def list_models(self):
        with self.rlock:
            return self.config.list_models()

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

    # get the context name
    def name(self):
        with self.rlock:
            return self.contextmgr.name()

    # set the context name
    def set_name(self, name):
        with self.rlock:
            self.contextmgr.set_name(name)

    # output current plan as status
    def status(self):
        with self.rlock:
            return self.contextmgr.status()
