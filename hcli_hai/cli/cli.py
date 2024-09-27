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

logging = logger.Logger()
logging.setLevel(logger.INFO)

class CLI:
    commands = None
    inputstream = None
    context = None
    message_tokens = 0
    context_tokens = 0
    total_tokens = 0
    max_context_length = 4097
    encoding_base = "p50k_base"
    context_file = None
    chat_file = None
    pwd = os.path.dirname(inspect.getfile(lambda: None))

    def __init__(self, commands, inputstream):
        self.commands = commands
        self.inputstream = inputstream
        self.chat_file = self.pwd + "/chat.output"
        self.context_file = self.pwd + "/context.json"

    def execute(self):
        self.read_context()

        if self.commands[1] == "chat":
            if len(self.commands) == 2:
                if self.inputstream != None:
                    return self.chat()

            elif len(self.commands) == 3 and self.commands[2] == "dump":
               if os.path.exists(self.chat_file):
                   f = open(self.chat_file, "rb")
                   return io.BytesIO(f.read())

        if self.commands[1] == "clear":
            with open(self.chat_file, "w") as f:
                f.write("")

            if os.path.exists(self.context_file):
                self.new_context()

        if self.commands[1] == "context":
            if self.commands[2] == "get":
                if os.path.exists(self.context_file):
                    f = open(self.context_file, "rb")
                    data = json.load(f)
                    return io.BytesIO(json.dumps(data, indent=4).encode('utf-8') + "\n".encode('utf-8'))

            if self.commands[2] == "set":
                if os.path.exists(self.context_file):
                    f = open(self.context_file, "w")
                    inputstream = self.inputstream.read().decode('utf-8')
                    self.context = json.loads(inputstream)
                    json.dump(self.context, f)
                    return None

        if self.commands[1] == "behavior":
            if self.commands[2] == "set":
                if os.path.exists(self.context_file):
                    f = open(self.context_file, "w")
                    inputstream = self.inputstream.read().decode('utf-8')
                    behavior = { "role" : "system", "content" : inputstream }
                    self.context[0] = behavior
                    json.dump(self.context, f)
                    return None

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

    def chat(self):
        inputstream = self.inputstream.read().decode('utf-8')
        if inputstream != "":
            question = { "role" : "user", "content" : inputstream }
            self.add(question)
            self.trim()

            if self.total_tokens != 0:
                try:
                    client = OpenAI(api_key=os.environ['OPENAI_API_KEY'])
                    response = client.chat.completions.create(
                                                               **instance.baseline,
                                                               messages=self.context
                                                           )
                except Exception as e:
                    return io.BytesIO(traceback.format_exc().encode("utf-8"))
            else:
                error = "ERROR: The token trim backoff reached 0. This means that you sent a stream that was too large to fit within the total allowable context limit of " + str(self.max_context_length) + " tokens, and the last trimming operation ended up completely wiping out the conversation context.\n"
                return io.BytesIO(error.encode("utf-8"))

            # Output for context retention
            output_response = response.choices[0].message
            self.add({ "role" : output_response.role, "content" : output_response.content})

            output = response.choices[0].message.content
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
