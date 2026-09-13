import io
import sys
import os
import re
import time
import inspect
import logger
from ai import ai
from ai import assistantrunner as a
#import runner as s
import threading

from datetime import datetime

from hcli_problem_details import *
from hcli_core.auth.cli.authenticator import deny_disabled_authentication

log = logger.Logger()


class Service:

    def __init__(self):

        self.waiting_for_update = False
        self.message_count_before_processing = 0

        self.ai = ai.AI()
#         self.runner = s.Runner()
        self.assistantrunner = a.AssistantRunner()
        self.assistant_thread = threading.Thread(target=self.assistant)
        self.assistant_thread.start()

        return

    def chat(self, inputstream):
        return self.ai.chat(inputstream)

    def async_chat(self, inputstream):
        data = inputstream.read()
        t = threading.Thread(target=self.ai.chat, args=(io.BytesIO(data),), daemon=True)
        t.start()
        return

    def get_context(self):
        return self.ai.get_context()

    def get_readable_context(self):
        return self.ai.get_readable_context()

    def name(self):
        return self.ai.name()

    def set_name(self, name):
        return self.ai.set_name(name)

    def ls(self):
        return self.ai.ls()

    def behavior(self, inputstream):
        return self.ai.behavior(inputstream)

    def new(self):
        return self.ai.new()

    def model(self):
        return self.ai.model()

    def list_models(self):
        return self.ai.list_models()

    def set_model(self, model):
        return self.ai.set_model(model)

    def provider(self):
        return self.ai.provider()

    def list_providers(self):
        return self.ai.list_providers()

    def set_provider(self, provider):
        return self.ai.set_provider(provider)

    def set(self, id):
        return self.ai.set(id)
        if self.assistantrunner.is_assisting() == True:
           self.assistantrunner.should_assist(False)
           time.sleep(0.5)
           self.assistantrunner.should_assist(True)

    def current(self):
        return self.ai.current()

    def rm(self, id):
        return self.ai.rm(id)

    def status(self):
        return self.ai.status()

    def reset(self):
        return self.ai.reset()

    def assist_speak(self, inputstream):
        inputstream = inputstream.read().decode('utf-8')
        if inputstream != "":
            inputstream = inputstream.rstrip()
            return self.assistantrunner.speak(inputstream)

#    @deny_disabled_authentication
#     def vibe(self, should_vibe):
#         self.runner.set_vibe(should_vibe)

#     def is_vibing(self):
#         return self.runner.is_vibing()

    # AssistantRunner controls
    def assist(self, should_assist):
        self.assistantrunner.set_assist(should_assist)

    def is_assisting(self):
        return self.assistantrunner.is_assisting()

    def assistant(self):
        lock = self.assistantrunner.lock
        if not lock.acquire(blocking=False):
            log.debug("[ hai ] assistant already running; exiting")
            return
        try:
            while True:

                if not self.assistantrunner.is_running and not self.assistantrunner.is_assisting():
                    self.waiting_for_update = False

                # First check if we're waiting for a previous command to finish
                if self.waiting_for_update:
                    current_count = len(self.ai.contextmgr.messages())
                    if current_count > self.message_count_before_processing:
                        # The message count has increased, so processing is complete
                        self.waiting_for_update = False
                        self.message_count_before_processing = 0
                    # Continue the main loop - don't process new commands while waiting
                    time.sleep(0.5)
                    continue

                # Regular processing logic
                if not self.assistantrunner.is_running and self.assistantrunner.is_assisting():
                    messages = self.ai.contextmgr.messages()

                    if messages and messages[-1]['role'] == 'assistant':
                        # Mark that we're waiting for this command to complete
                        self.message_count_before_processing = len(messages)
                        self.waiting_for_update = True
                        self.assistantrunner.run(messages)

                time.sleep(0.5)
        finally:
            lock.release()

    def harness(self):
        pass
#         with self.runner.lock:
#             while True:
# 
#                 if not self.runner.is_running and not self.runner.is_vibing():
#                     self.ai.contextmgr.set_status("")
#                     self.waiting_for_update = False
# 
#                 # First check if we're waiting for a previous command to finish
#                 if self.waiting_for_update:
#                     current_count = len(self.runner.ai.contextmgr.messages())
#                     if current_count > self.message_count_before_processing:
#                         # The message count has increased, so processing is complete
#                         self.waiting_for_update = False
#                         self.message_count_before_processing = 0
#                     # Continue the main loop - don't process new commands while waiting
#                     time.sleep(0.5)
#                     continue
# 
#                 # Regular processing logic
#                 if not self.runner.is_running and self.runner.is_vibing():
#                     messages = self.runner.ai.contextmgr.messages()
# 
#                     if messages and messages[-1]['role'] == 'assistant':
#                         command = self.runner.get_plan()
#                         if command != "":
# 
#                             # Mark that we're waiting for this command to complete
#                             self.message_count_before_processing = len(messages)
#                             self.waiting_for_update = True
#                             self.runner.run(command)
# 
#                 time.sleep(0.5)
