import json
import io
import os
import sys
import inspect
import traceback
import tiktoken
import logger
import ai

from anthropic import Anthropic

logging = logger.Logger()
logging.setLevel(logger.INFO)


class CLI:
    commands = None
    inputstream = None

    def __init__(self, commands, inputstream):
        self.commands = commands
        self.inputstream = inputstream
        self.ai = ai.AI()

    def execute(self):
        if len(self.commands) == 1:
            if self.inputstream is not None:
                response = self.ai.chat(self.inputstream)
                return io.BytesIO(response.encode('utf-8') + "\n".encode('utf-8'))

        if self.commands[1] == "clear":
            self.ai.clear()
            return

        if self.commands[1] == "context":
            if len(self.commands) == 2:
                context = self.ai.get_context()
                return io.BytesIO(context.serialize().encode('utf-8') + "\n".encode('utf-8'))
            if len(self.commands) == 3:
                readable_context = self.ai.get_readable_context()
                return io.BytesIO(readable_context.encode('utf-8') + "\n".encode('utf-8'))

        if self.commands[1] == "ls":
            contexts = self.ai.ls()
            return io.BytesIO(json.dumps(contexts, indent=4).encode('utf-8') + "\n".encode('utf-8'))

        if self.commands[1] == "new":
            current = self.ai.new()
            return io.BytesIO(current.encode('utf-8') + "\n".encode('utf-8'))

        if self.commands[1] == "current":
            current = self.ai.current()
            return io.BytesIO(current.encode('utf-8') + "\n".encode('utf-8'))

        if self.commands[1] == "behavior":
            if self.inputstream is not None:
                self.ai.behavior(self.inputstream)
            return

        if self.commands[1] == "name":
            if len(self.commands) == 2:
                name = self.ai.name()
                if name is not None:
                    return io.BytesIO(name.encode('utf-8') + "\n".encode('utf-8'))
                else:
                    return io.BytesIO(b"None\n")

            if len(self.commands) == 4:
                if self.commands[2] == "set":
                    self.ai.set_name(self.commands[3])
                    return None

        if self.commands[1] == "model":
            if len(self.commands) == 2:
                model = self.ai.model()
                if model is not None:
                    return io.BytesIO(model.encode('utf-8') + "\n".encode('utf-8'))
                else:
                    return io.BytesIO(b"None\n")

            if len(self.commands) == 3:
                if self.commands[2] == "ls":
                    models = self.ai.list_models()
                    return io.BytesIO(json.dumps(models, indent=4).encode('utf-8') + "\n".encode('utf-8'))

            if len(self.commands) == 4:
                if self.commands[2] == "set":
                    self.ai.set_model(self.commands[3])
                    return

        if self.commands[1] == "set":
            if len(self.commands) == 3:
                self.ai.set(self.commands[2])
                return

        if self.commands[1] == "rm":
            if len(self.commands) == 3:
                self.ai.rm(self.commands[2])
                return

        return None
