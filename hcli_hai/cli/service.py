import io
import sys
import os
import re
import time
import inspect
import logger
from ai import ai
from ai.assistant import assistant as asr
from ai.orchestrator import orchestrator as o
from ai.orchestrator.router import froute, skill_area
from ai.provider import provider as p
import threading

from datetime import datetime

from hcli_problem_details import *
from hcli_core.auth.cli.authenticator import deny_disabled_authentication

log = logger.Logger()


class Service:

    def __init__(self):

        self.ai = ai.AI()
        self.provider = p.Provider()

        self.assistant = asr.Assistant()
        self.assistant_thread = threading.Thread(target=self.run_assist)
        self.assistant_thread.start()
        self.orchestrator = o.Orchestrator()

        return

    def chat(self, inputstream):
        try:
            is_vibing = self.orchestrator.is_vibing()
            if is_vibing:
                pending = self.orchestrator.pending_bash()
                if pending:
                    # already emitted a command; do not plan again and do not talk
                    msg = "agent waiting for observation via hai agent next"
                    log.error(msg)
                    raise ConflictError(detail=msg)

            text = inputstream.read().decode('utf-8')
            if text == "" or text is None:
                return None

            text = self.ai.consume_request(text)

            if is_vibing:
                decision = froute(text)
                if decision == "do":
                    self.orchestrator.harness()
                    return None

            response = self.ai.process_request()

            if response is None:
                return None

            self.ai.commit_response(response)
            return response
        except Exception as e:
            raise e

    def async_chat(self, inputstream):
        data = inputstream.read()
        t = threading.Thread(target=self.chat, args=(io.BytesIO(data),), daemon=True)
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

    def title(self):
        return self.ai.title()

    def set_title(self, title):
        return self.ai.set_title(title)

    def auto_title(self):
        title = self.ai.generate_title()
        self.ai.set_title(title)
        return title

    def ls(self):
        return self.ai.ls()

    def behavior(self, inputstream):
        return self.ai.behavior(inputstream)

    def new(self):
        return self.ai.new()

    def get_model(self):
        return self.provider.get_model()

    def list_models(self):
        return self.provider.list_models()

    def set_model(self, model):
        return self.provider.set_model(model)

    def get_provider(self):
        return self.provider.get_provider()

    def list_providers(self):
        return self.provider.list_providers()

    def set_provider(self, provider):
        return self.provider.set_provider(provider)

    def set(self, id):
        if self.assistant.is_assisting() == True:
            self.previous_response = None
        return self.ai.set(id)

    def current(self):
        return self.ai.current()

    def rm(self, id):
        return self.ai.rm(id)

    def reset(self):
        return self.ai.reset()

    def assist_speak(self, inputstream):
        inputstream = inputstream.read().decode('utf-8')
        if inputstream != "":
            inputstream = inputstream.rstrip()
            return self.assistant.speak(inputstream)

    # Assistant controls
    def assist(self, should_assist):
        data = should_assist

        t = threading.Thread(
            target=self.assistant.set_assist,
            args=(data,),
            daemon=True
        )

        t.start()

    def pause_voice(self):
        self.assistant.pause_voice()

    def resume_voice(self):
        self.assistant.resume_voice()

    def is_assisting(self):
        return self.assistant.is_assisting()

    def run_assist(self):
        lock = self.assistant.lock
        if not lock.acquire(blocking=False):
            log.debug("assistant already running; exiting")
            return
        try:
            ar = self.assistant
            while True:
                if not ar.is_running and ar.is_assisting():
                    ar.try_assist(self.ai.contextmgr.messages())
                time.sleep(0.5)
        finally:
            lock.release()

    # Orchestrator controls.
    def agent(self, should_vibe):
        data = should_vibe

        t = threading.Thread(
            target=self.orchestrator.set_vibe,
            args=(data,),
            daemon=True
        )

        t.start()

    def status(self):
        return self.orchestrator.status()

    def task(self):
        return self.orchestrator.task()

    def plan(self):
        return self.orchestrator.public()

    def next(self, inputstream):
        if inputstream is None:
            observation = ""
        else:
            observation = inputstream.read().decode("utf-8")
        self.orchestrator.next(observation)
        self.orchestrator.mark()
        return

    def async_next(self, inputstream):
        data = inputstream.read()
        t = threading.Thread(target=self.next, args=(io.BytesIO(data),), daemon=True)
        t.start()
        return

    def mark(self):
        return self.orchestrator.mark()

    def is_vibing(self):
        return self.orchestrator.is_vibing()

    def list_skills(self):
        return self.orchestrator.list_skills()

    def set_skill(self, skill):
        return self.orchestrator.set_skill(skill)

    def add_input_artifact(self, inputstream, filename=None):
        return self.orchestrator.add_input_artifact(
            inputstream=inputstream,
            filename=filename,
        )

    def list_input_artifacts(self):
        return self.orchestrator.list_input_artifacts()

    def remove_input_artifact(self, artifact_id):
        return self.orchestrator.remove_input_artifact(artifact_id)

    def reset_input_artifacts(self):
        return self.orchestrator.reset_input_artifacts()

    def list_output_artifacts(self):
        return self.orchestrator.list_output_artifacts()

    def pull_output_artifact(self, artifact_id):
        return self.orchestrator.pull_output_artifact(artifact_id)

    def remove_output_artifact(self, artifact_id):
        return self.orchestrator.remove_output_artifact(artifact_id)

    def reset_output_artifacts(self):
        return self.orchestrator.reset_output_artifacts()
