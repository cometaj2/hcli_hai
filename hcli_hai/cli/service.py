import io
import sys
import os
import re
import time
import inspect
import logger
from ai import ai
from ai import assistantrunner as asr
from ai import agentrunner as agr
from ai import orchestrator as orch
from ai.router import froute
import threading

from datetime import datetime

from hcli_problem_details import *
from hcli_core.auth.cli.authenticator import deny_disabled_authentication

log = logger.Logger()


class Service:

    def __init__(self):

        self.ai = ai.AI()

        self.assistantrunner = asr.AssistantRunner()
        self.assistant_thread = threading.Thread(target=self.assistant)
        self.assistant_thread.start()

        self.agentrunner = agr.AgentRunner()
        self.orchestrator = orch.Orchestrator()

        return

    def chat(self, inputstream):
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
                # plan is in hai agent plan; do not commit JSON into the chat transcript
                return None

        response = self.ai.process_request()

        if response is None:
            return None

        self.ai.commit_response(response)
        return response

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
        if self.assistantrunner.is_assisting() == True:
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
            return self.assistantrunner.speak(inputstream)

    # AssistantRunner controls
    def assist(self, should_assist):
        self.assistantrunner.set_assist(should_assist)

    def is_assisting(self):
        return self.assistantrunner.is_assisting()

    def assistant(self):
        lock = self.assistantrunner.lock
        if not lock.acquire(blocking=False):
            log.debug("assistant already running; exiting")
            return
        try:
            ar = self.assistantrunner
            while True:
                if not ar.is_running and ar.is_assisting():
                    ar.try_assist(self.ai.contextmgr.messages())
                time.sleep(0.5)
        finally:
            lock.release()

    # Agent controls. The orchestrator plans tasks; AgentRunner executes the active task.
    def agent(self, should_vibe):
        self.orchestrator.set_vibe(should_vibe)

    def status(self):
        return self.orchestrator.status()

    def task(self):
        return self.orchestrator.task()

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
