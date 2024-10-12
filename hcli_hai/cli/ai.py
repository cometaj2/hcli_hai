import json
import io
import os
import sys
import inspect
import traceback
import logger
import model
import config
import context
import config
import uuid
import shutil

from datetime import datetime
from openai import OpenAI
from anthropic import Anthropic

logging = logger.Logger()


class AI:
    config = None
    context = None

    def __init__(self):
        self.config = config.Config()
        self.context = context.Context()

    def chat(self, inputstream):
        inputstream = inputstream.read().decode('utf-8')
        if inputstream != "":
            question = { "role" : "user", "content" : inputstream }
            self.context.append(question)
            self.context.trim()

            if self.context.total_tokens != 0:
                try:
                    #client = OpenAI(api_key=os.environ['OPENAI_API_KEY'])
                    #response = client.chat.completions.create(
                    #                                           **model.openai_baseline,
                    #                                           messages=self.context
                    #                                       )
                    client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

                    # Separate system message from user messages
                    system_message = next((msg["content"] for msg in self.context.messages() if msg["role"] == "system"), "")
                    user_messages = [msg for msg in self.context.messages() if msg["role"] != "system"]

                    response = client.messages.create(
                                                         **model.claude_3_5_sonnet_20240620,
                                                         system=system_message,
                                                         messages=user_messages
                                                     )

                    logging.debug(response)

                except Exception as e:
                    return io.BytesIO(traceback.format_exc().encode("utf-8"))
            else:
                error = "The token trim backoff reached 0. This means that you sent a stream that was too large to fit within the total allowable context limit of " + str(self.context.max_context_length) + " tokens, and the last trimming operation ended up completely wiping out the conversation context.\n"
                return io.BytesIO(error.encode("utf-8"))

#             # Output for context retention
#             output_response = response.choices[0].message
            output_response = response

            # Extract the text content from the response
            output_content = " ".join([block.text for block in output_response.content if block.type == 'text'])

            self.context.append({ "role" : output_response.role, "content" : output_content})

            output = output_content
            output = output + "\n"

            self.context.generate_title()
            self.context.save()

            return io.BytesIO(output.encode("utf-8"))

    def get_context(self):
        return self.context.get_context()

    def get_readable_context(self):
        return self.context.get_readable_context()

    def clear(self):
        self.context.clear()

    def behavior(self, inputstream):
        self.context.behavior(inputstream)

    def ls(self):
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
                            data = json.load(f)
                            title = data.get('title', item)  # Use directory name as fallback
                            update_time = os.path.getmtime(context_file)
                            contexts.append({
                                'context_id': item,
                                'title': title,
                                'update_time': update_time
                            })
                    except json.JSONDecodeError:
                        # If there's an error reading the JSON, just use the directory name
                        contexts.append({
                            'context_id': item,
                            'title': 'N/A',
                            'update_time': os.path.getmtime(context_file) if os.path.exists(context_file) else 0
                        })
                else:
                    # If context.json doesn't exist, use the directory name as both name and title
                    contexts.append({
                        'context_id': item,
                        'title': 'N/A',
                        'update_time': 0
                    })

        # Sort contexts by creation time, newest first
        sorted_contexts = sorted(contexts, key=lambda x: x['update_time'], reverse=False)

        # Format the creation time and remove it from the final output
        for context in sorted_contexts:
            context['update_time'] = datetime.fromtimestamp(context['update_time']).strftime('%Y-%m-%d %H:%M:%S')

        return sorted_contexts

    def set(self, id):
        contexts = self.ls()
        context_ids = [context['context_id'] for context in contexts]
        if id in context_ids:
            self.context.set(id)
        else:
            logging.warning("provided context id is not found in available contexts.")

    def new(self):
        if os.path.exists(self.config.dot_hai_config_file):
            os.remove(self.config.dot_hai_config_file)
        self.config.init()
        self.context.init()

        return self.current()

    def rm(self, id):
        context_folder = os.path.join(self.config.dot_hai_context, id)
        if os.path.exists(context_folder):
            shutil.rmtree(context_folder)
            logging.info("removed " + context_folder)

    def current(self):
        return self.config.context
