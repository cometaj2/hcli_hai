import json
import io
import os
import sys
import re
import inspect
import traceback
import logger
import config
import context as c
import shutil
import time
import threading

from datetime import datetime
from anthropic import Anthropic
from model import models


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
        logging.info("[ hai ] Initializing AI singleton")
        self.config = config.Config()
        self.contextmgr = c.ContextManager()
        logging.info(f"[ hai ] AI initialization complete: config={bool(self.config)}, contextmgr={bool(self.contextmgr)}")

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
                    logging.info("[ hai ] Request  - total context tokens: " + str(tokens['total_tokens']))

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
                        warning = "[ hai ] The token trim backoff completely collapsed. This means that the stream was too large to fit within the total allowable context limit of " + str(self.contextmgr.counter.max_context_length) + " tokens, and the last trimming operation ended up completely wiping out the remaining conversation context."
                        logging.warning(warning)
                        self.contextmgr.save()

                        return warning

                    output_response = response

                    # Extract the text content from the response
                    output_content = " ".join([block.text for block in output_response.content if block.type == 'text'])

                    self.contextmgr.append({ "role" : output_response.role, "content" : output_content})

                    tokens = self.contextmgr.counter.get_stats(self.contextmgr.context)
                    logging.info("[ hai ] Response - total context tokens: " + str(tokens['total_tokens']))

                    output = output_content

                    self.contextmgr.generate_title()
                    self.contextmgr.save()

                    return output
            else:
                warning = "[ hai ] No model selected. Select one from the list of models."
                logging.warning(warning)
                return warning

    def get_context(self):
        with self.rlock:
            return self.contextmgr.get_context()

    def get_readable_context(self):
        with self.rlock:
            return self.contextmgr.get_readable_context()

    def clear(self):
        with self.rlock:
            self.contextmgr.clear()

    def behavior(self, inputstream):
        with self.rlock:
            self.contextmgr.behavior(inputstream)

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

    def set(self, context_id):
        with self.rlock:
            contexts = self.ls()
            context_ids = [context['context_id'] for context in contexts]
            if context_id in context_ids:
                self.contextmgr.set(context_id)
            else:
                logging.warning(context_id)
                logging.warning("[ hai ] Provided context id is not found in available contexts.")

    def new(self):
        with self.rlock:
            if os.path.exists(self.config.dot_hai_config_file):
                os.remove(self.config.dot_hai_config_file)
            self.config.init()
            self.contextmgr.init()

            return self.current()

    def rm(self, context_id):
        with self.rlock:
            context_folder = os.path.join(self.config.dot_hai_context, context_id)
            if os.path.exists(context_folder):
                shutil.rmtree(context_folder)
                logging.info("[ hai ] Removed " + context_folder)

    def current(self):
        with self.rlock:
            return self.config.context

    def list_models(self):
        with self.rlock:
            return self.config.list_models()

    def model(self):
        with self.rlock:
            return self.config.model

    def set_model(self, model):
        with self.rlock:
            models = self.list_models()
            if model in models:
                self.config.model = model

    def name(self):
        with self.rlock:
            return self.contextmgr.name()

    def set_name(self, name):
        with self.rlock:
            self.contextmgr.set_name(name)

#     def vibe(self):
#         self.behavior(io.BytesIO(b.hcli_integration_behavior.encode('utf-8')))
#         messages = self.contextmgr.messages()
#         if messages:
#             # Get the last item using negative indexing
#             last_message = messages[-1]
#             logging.info("[ hai ] Attempting to vibe...")
#             if last_message['role'] != "system":
#                 # Look for all hcli_integration code blocks
#                 pattern = r"```python\s*def\s+hcli_integration\(\):\s*cli\([\"'](.+?)[\"']\)"
#                 matches = re.findall(pattern, last_message['content'], re.DOTALL)
# 
#                 # Check if we have at least one match
#                 if matches:
#                     singular = len(matches) == 1
#                     if not singular:
#                         logging.warning(f"[ hai ] Found {len(matches)} hcli_integration blocks, expected exactly 1.")
# 
#                     # Only execute the first match regardless of how many there are
#                     command = matches[0]  # Extract the first command inside cli()
#                     logging.info(f"[ hai ] Executing first command: {command}")
# 
#                     stdout = ""
#                     stderr = ""
#                     try:
#                         chunks = cli(command)
#                         for dest, chunk in chunks:
#                             if dest == 'stdout':
#                                 stdout = stdout + chunk.decode()
#                             elif dest == 'error':
#                                 stderr = stderr + chunk.decode()
#                     except Exception as e:
#                         stderr = repr(e)
# 
#                     try:
#                         if stderr == "":
#                             if stdout == "":
#                                 stdout = "silence is success"
#                             if not singular:
#                                 stdout = b.singular_integration_block_behavior + stdout
#                             logging.debug(stdout)
#                             self.chat(io.BytesIO(stdout.encode('utf-8')))
#                         else:
#                             if not singular:
#                                 stderr = b.singular_integration_block_behavior + stderr
#                             logging.debug(stderr)
#                             self.chat(io.BytesIO(stderr.encode('utf-8')))
#                     except Exception as e:
#                         stderr = repr(e)
#                         logging.debug(stderr)
#                         self.chat(io.BytesIO(stderr.encode('utf-8')))
#                 else:
#                     logging.warning("[ hai ] Can't vibe without hcli_integration conversation cues.")
