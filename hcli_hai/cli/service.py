import io
import sys
import os
import re
import time
import inspect
import logger
import runner as s
import jobqueue as j
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.executors.pool import ThreadPoolExecutor
from collections import OrderedDict

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
        self.runner = s.Runner()
        self.job_queue = j.JobQueue()
        process = self.schedule(self.process_job_queue)
        scheduler.start()

        return

    # we schedule immediate single instance job executions.
    def schedule(self, function):
        return scheduler.add_job(function, 'date', run_date=datetime.now(), max_instances=1)

    def jobs(self):
        result = {}
        jobs = self.job_queue.list()
        for i, job in enumerate(jobs, start=1):
            result[i] = job[0]  # Use integer keys instead of strings

        reversal = OrderedDict(sorted(result.items(), key=lambda x: x[0], reverse=True))

        if reversal:
            logging.info("[ hai ] ------------------------------------------")
            for key, value in reversal.items():
                logging.info(f"[ hai ] job {key}: {value}")

        return reversal

    def vibe(self, should_vibe):
        self.runner.set_vibe(should_vibe)

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
                if not self.runner.is_running and self.runner.is_vibing:
                    messages = self.runner.ai.contextmgr.messages()

                    if messages and messages[-1]['role'] == 'assistant':
                        command = self.runner.get_plan()
                        if command != "":

                            # Mark that we're waiting for this command to complete
                            self.message_count_before_processing = len(messages)
                            self.waiting_for_update = True
                            self.runner.run(command)
                            # Don't wait here - we'll check in the next loop iteration

                time.sleep(0.5)
