import json
import io
import os
import sys
import inspect
import traceback
import tiktoken
import logger
import instance
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
        self.ai.read_context()

        if self.commands[1] == "chat":
            if len(self.commands) == 2:
                if self.inputstream != None:
                    return self.ai.chat(self.inputstream)

            elif len(self.commands) == 3 and self.commands[2] == "dump":
               return self.ai.dump()

        if self.commands[1] == "clear":
            self.ai.clear()

        if self.commands[1] == "context":
            if self.commands[2] == "get":
                return self.ai.context_get()

            if self.commands[2] == "set":
                return self.ai.context_set(self.inputstream)

        if self.commands[1] == "behavior":
            if self.commands[2] == "set":
                return self.ai.behavior_set(self.inputstream)

        return None
