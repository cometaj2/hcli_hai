import io
import sys
import os
import re
import time
import inspect
import logger
from ai import ai
import runner as s
import jobqueue as j

from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.executors.pool import ThreadPoolExecutor
from collections import OrderedDict

from hcli_problem_details import *

logging = logger.Logger()


class Service:
    scheduler = None

    def __init__(self):
        global scheduler

        executors = {
            'default': ThreadPoolExecutor(10)
        }

        self.waiting_for_update = False
        self.message_count_before_processing = 0

        scheduler = BackgroundScheduler(executors=executors)
        self.ai = ai.AI()
        self.runner = s.Runner()
        self.job_queue = j.JobQueue()
        process = self.schedule(self.process_job_queue)
        scheduler.start()

        return

    # we schedule immediate single instance job executions.
    def schedule(self, function):
        return scheduler.add_job(function, 'date', run_date=datetime.now(), max_instances=1)

    # AI controls
    def chat(self, inputstream):
        return self.ai.chat(inputstream)

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

    def new(self):
        if not self.runner.is_vibing():
            return self.ai.new()
        else:
            msg = "cannot change context while vibing. disable vibing before changing context."
            logging.error(msg)
            raise ConflictError(detail="hai: " + msg)

    def model(self):
        return self.ai.model()

    def list_models(self):
        return self.ai.list_models()

    def set(self, id):
        if not self.runner.is_vibing():
            return self.ai.set(id)
        else:
            msg = "cannot change context while vibing. disable vibing before changing context."
            logging.error(msg)
            raise ConflictError(detail="hai: " + msg)

    def current(self):
        return self.ai.current()

    def rm(self, id):
        return self.ai.rm(id)

    def set_model(self, model):
        return self.ai.set_model(model)

    def status(self):
        return self.ai.status()

    def clear(self):
        if not self.runner.is_vibing():
            return self.ai.clear()
        else:
            msg = "cannot clear the current context while vibing. disable vibing before clearing context."
            logging.error(msg)
            raise ConflictError(detail="hai: " + msg)

    # Runner controls
    def vibe(self, should_vibe):
        self.runner.set_vibe(should_vibe)

    def is_vibing(self):
        return self.runner.is_vibing()

    def process_job_queue(self):
        with self.runner.lock:
            while True:

                # First check if we're waiting for a previous command to finish
                if self.waiting_for_update:
                    current_count = len(self.runner.ai.contextmgr.messages())
                    if current_count > self.message_count_before_processing:
                        # The message count has increased, so processing is complete
                        self.waiting_for_update = False
                        self.message_count_before_processing = 0
                    # Continue the main loop - don't process new commands while waiting
                    time.sleep(0.5)
                    continue

                # Regular processing logic
                if not self.runner.is_running and self.runner.is_vibing():
                    messages = self.runner.ai.contextmgr.messages()

                    if messages and messages[-1]['role'] == 'assistant':
                        command = self.runner.get_plan()
                        if command != "":

                            # Mark that we're waiting for this command to complete
                            self.message_count_before_processing = len(messages)
                            self.waiting_for_update = True
                            self.runner.run(command)

                time.sleep(0.5)
