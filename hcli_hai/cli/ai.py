import json
import io
import os
import sys
import inspect
import traceback
import tiktoken
import logger
import instance

from openai import OpenAI
from anthropic import Anthropic

logging = logger.Logger()
logging.setLevel(logger.INFO)


class AI:
    context = None
    message_tokens = 0
    context_tokens = 0
    total_tokens = 0
    max_context_length = 10000
    encoding_base = "p50k_base"
    context_file = None
    chat_file = None
    pwd = os.path.dirname(inspect.getfile(lambda: None))

    def __init__(self):
        self.chat_file = self.pwd + "/chat.output"
        self.context_file = self.pwd + "/context.json"

    def dump(self):
       if os.path.exists(self.chat_file):
           f = open(self.chat_file, "rb")
           return io.BytesIO(f.read())

    def clear(self):
        with open(self.chat_file, "w") as f:
            f.write("")

        if os.path.exists(self.context_file):
            self.new_context()

    def context_get(self):
        if os.path.exists(self.context_file):
            f = open(self.context_file, "rb")
            data = json.load(f)
            return io.BytesIO(json.dumps(data, indent=4).encode('utf-8') + "\n".encode('utf-8'))

    def context_set(self, inputstream):
        if os.path.exists(self.context_file):
            f = open(self.context_file, "w")
            inputstream = inputstream.read().decode('utf-8')
            self.context = json.loads(inputstream)
            json.dump(self.context, f)
            return None

    def behavior_set(self, inputstream):
        if os.path.exists(self.context_file):
            f = open(self.context_file, "w")
            inputstream = inputstream.read().decode('utf-8')
            behavior = { "role" : "system", "content" : inputstream }
            self.context[0] = behavior
            json.dump(self.context, f)
            return None

    def add(self, entry):
        self.context.append(entry)

    def count(self):
        try:
            with open(self.context_file, 'r') as f:
                encoding = tiktoken.get_encoding(self.encoding_base)

                self.message_tokens = 0
                for item in self.context:
                    if "content" in item:
                        self.message_tokens += len(encoding.encode(item["content"]))

                self.context_tokens = 0
                for item in self.context:
                    self.context_tokens += len(encoding.encode(json.dumps(item)))

                self.total_tokens = self.message_tokens + self.context_tokens
                message = "Context tokens: " + str(self.total_tokens)
                logging.info(message)

                if self.total_tokens > self.max_context_length:
                    return True
        except:
            self.new_context()

        return False

    def trim(self):
        while(self.count()):
            self.context.pop(1)
            message = "Context tokens: " + str(self.total_tokens) + ". Trimming the oldest entries to remain under " + str(self.max_context_length) + " tokens."
            logging.info(message)

    def read_context(self):
        if os.path.exists(self.context_file):
            try:
                with open(self.context_file, 'r') as f:
                    self.context = json.load(f)
            except:
                self.new_context()
        else:
            self.new_context()

    def new_context(self):
        with open(self.context_file, 'w') as f:
            self.context = [{ "role" : "system", "content" : "" }]
            json.dump(self.context, f)

        self.total_tokens = 0

    def chat(self, inputstream):
        inputstream = inputstream.read().decode('utf-8')
        if inputstream != "":
            question = { "role" : "user", "content" : inputstream }
            self.add(question)
            self.trim()

            if self.total_tokens != 0:
                try:
                    #client = OpenAI(api_key=os.environ['OPENAI_API_KEY'])
                    #response = client.chat.completions.create(
                    #                                           **instance.openai_baseline,
                    #                                           messages=self.context
                    #                                       )
                    client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

                    # Separate system message and user messages
                    system_message = next((msg["content"] for msg in self.context if msg["role"] == "system"), "")
                    user_messages = [msg for msg in self.context if msg["role"] != "system"]

                    response = client.messages.create(
                                                         **instance.anthropic_claude_3_5_sonnet,
                                                         system=system_message,
                                                         messages=user_messages
                                                     )

                    logging.info(response)

                except Exception as e:
                    return io.BytesIO(traceback.format_exc().encode("utf-8"))
            else:
                error = "ERROR: The token trim backoff reached 0. This means that you sent a stream that was too large to fit within the total allowable context limit of " + str(self.max_context_length) + " tokens, and the last trimming operation ended up completely wiping out the conversation context.\n"
                return io.BytesIO(error.encode("utf-8"))

#             # Output for context retention
#             output_response = response.choices[0].message
            output_response = response

            # Extract the text content from the response
            output_content = " ".join([block.text for block in output_response.content if block.type == 'text'])

            self.add({ "role" : output_response.role, "content" : output_content})

            output = output_content
            output = output + "\n"

            # Ouput for human consumption and longstanding conversation tracking
            with io.open(self.chat_file, 'a') as f:
                f.write("----Question:\n\n")
                f.write(inputstream + "\n")
                f.write("----Answer:\n\n")
                f.write(output + "\n")
                f.close()

            with open(self.context_file, 'w') as f:
                json.dump(self.context, f)

            return io.BytesIO(output.encode("utf-8"))
