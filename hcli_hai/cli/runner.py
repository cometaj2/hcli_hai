import io
import logger
import threading
import time
import re
from ai import behavior as b
from ai import ai
from huckle import cli, stdin
import xml.etree.ElementTree as et

logging = logger.Logger()

# Singleton Runner
class Runner:
    instance = None
    is_running = False
    lock = None
    terminate = None
    _is_vibing = False
    ai = None

    def __new__(self):
        if self.instance is None:
            self.instance = super().__new__(self)
            self.lock = threading.Lock()
            self.rlock = threading.RLock()
            self.ai = ai.AI()
            self.exception_event = threading.Event()
            self.terminate = False
            self._is_vibing = False

        return self.instance

    def set_vibe(self, should_vibe):
        with self.rlock:
            self._is_vibing = should_vibe
            if should_vibe is True:
                self.ai.behavior(io.BytesIO(b.hcli_integration_behavior.encode('utf-8')))
                logging.info(f"[ hai ] Vibe runner started.")
            else:
                logging.info(f"[ hai ] Vibe runner stopped.")

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
                            logging.info(f"[ hai ] hcli integration: {command}")
                            return command
                        else:
                            logging.debug("[ hai ] Unable to vibe without a plan with hcli tags.")
                            self.ai.contextmgr.set_status("")
                            return ""
                    except et.ParseError as e:
                        logging.warning(f"[ hai ] Failed to parse XML plan: {e}")
                        self.ai.contextmgr.set_status("")
                        return ""
                else:
                    logging.debug("[ hai ] No plan found in the message content.")
                    self.ai.contextmgr.set_status("")
                    return ""
        return ""

    def run(self, command):
        self.is_running = True
        self.terminate = False

        try:
            logging.info("[ hai ] Attempting to vibe...")
            stdout = ""
            stderr = ""
            try:
                chunks = cli(command)
                for dest, chunk in chunks:
                    print(dest)
                    print(chunk.decode())
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
                    logging.debug(stdout)
                    self.ai.chat(io.BytesIO(stdout.encode('utf-8')))
                else:
                    logging.debug(stderr)
                    self.ai.chat(io.BytesIO(stderr.encode('utf-8')))
            except Exception as e:
                stderr = repr(e)
                logging.debug(stderr)
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
            raise TerminationException("[ hc ] terminated")

    def abort(self):
        self.is_running = False
        self.terminate = False

class TerminationException(Exception):
    pass
