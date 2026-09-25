import io
import logger
import threading
import time
import re
import config as a
from ai import agentbehavior as b
from ai import ai
from huckle import cli, stdin
import xml.etree.ElementTree as et

log = logger.Logger()


# Singleton AgentRunner
class AgentRunner:
    _instance = None
    _init_lock = threading.RLock()

    def __new__(cls):
        with cls._init_lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance.initialized = False
            return cls._instance

    def __init__(self):
        if self.initialized:
            return
        with self._init_lock:
            if self.initialized:
                return
            self.rlock = threading.RLock()
            self.lock = threading.RLock()

            self.is_running = False
            self.config = a.Config()

            self._is_vibing = False
            self.initialized = True
            self.terminate = False
            self.assist_key = None

            self.ai = ai.AI()

    def set_vibe(self, should_vibe):
        with self.rlock:
            self._is_vibing = should_vibe
            if should_vibe is True:
                self.ai.behavior(io.BytesIO(b.hcli_integration_behavior.encode('utf-8')))
                log.info(f"Vibe runner started.")
            else:
                log.info(f"Vibe runner stopped.")

    def is_vibing(self):
        with self.rlock:
            return self._is_vibing

    def get_plan(self):
        self.ai.contextmgr.get_context()
        messages = self.ai.contextmgr.messages()
        if messages:
            last_message = messages[-1]
            if last_message['role'] == "assistant":
                content = last_message['content']

                # Use regex to extract the first <plan> element
                plan_pattern = r'<plan>.*?</plan>'
                match = re.search(plan_pattern, content, re.DOTALL)

                if match:
                    plan_content = match.group(0)
                    log.info(match)
                    log.info(plan_content)
                    try:
                        # Parse just the extracted plan with XML
                        plan_elem = et.fromstring(plan_content)

                        # Clear any unwanted text if needed (though regex should have isolated the plan)
                        if plan_elem.text and not plan_elem.text.strip():
                            plan_elem.text = None

                        plan_string = et.tostring(plan_elem, encoding='utf-8', method='xml')
                        self.ai.contextmgr.set_status(plan_string.decode())

                        # Look for hcli tags within the plan
                        hcli_elem = plan_elem.find('.//hcli[1]')
                        if hcli_elem is not None:
                            command = hcli_elem.text.strip() if hcli_elem.text else ""
                            log.info(f"hcli integration: {command}")
                            return command
                        else:
                            log.debug("Unable to vibe without a plan with hcli tags.")
                            self.ai.contextmgr.set_status("")
                            return ""
                    except et.ParseError as e:
                        log.warning(f"Failed to parse XML plan: {e}")
                        self.ai.contextmgr.set_status("")
                        return ""
                else:
                    log.debug("No plan found in the message content.")
                    self.ai.contextmgr.set_status("")
                    return ""
        return ""

    def run(self, command):
        self.is_running = True
        self.terminate = False

        try:
            log.info("Attempting to vibe...")
            stdout = ""
            stderr = ""
            try:
                chunks = cli(command)
                for dest, chunk in chunks:
                    if dest == 'stdout':
                        stdout = stdout + chunk.decode()
                    elif dest == 'stderr':
                        stderr = stderr + chunk.decode()
            except Exception as e:
                stderr = repr(e)

            try:
                if stderr == "":
                    if stdout == "":
                        stdout = "silence is success"
                    log.debug(stdout)
                    self.ai.chat(io.BytesIO(stdout.encode('utf-8')))
                else:
                    log.debug(stderr)
                    self.ai.chat(io.BytesIO(stderr.encode('utf-8')))
            except Exception as e:
                stderr = repr(e)
                log.debug(stderr)
                self.ai.chat(io.BytesIO(stderr.encode('utf-8')))
        except TerminationException as e:
            self.abort()
        except Exception as e:
            self.abort()
        finally:
            self.terminate = False
            self.is_running = False

        return

    def check_termination(self):
        if self.terminate:
            raise TerminationException("terminated")

    def abort(self):
        self.is_running = False
        self.terminate = False

class TerminationException(Exception):
    pass
