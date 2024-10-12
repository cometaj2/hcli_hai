import json
import io
import os
import sys
import inspect
import traceback
import tiktoken
import logger
import ai

from openai import OpenAI
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
            if self.inputstream != None:
                return self.ai.chat(self.inputstream)

        if self.commands[1] == "clear":
            self.ai.clear()
            return

        if self.commands[1] == "context":
            if len(self.commands) == 2:
                context = self.ai.get_context()
                return io.BytesIO(json.dumps(context, indent=4).encode('utf-8') + "\n".encode('utf-8'))
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
            self.ai.behavior(self.inputstream)
            return

        if len(self.commands) == 3:
            if self.commands[1] == "set":
                self.ai.set(self.commands[2])

            if self.commands[1] == "rm":
                self.ai.rm(self.commands[2])

        return None
