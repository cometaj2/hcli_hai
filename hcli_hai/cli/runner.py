import io
import logger
import threading
import time
import behavior as b
import ai
from huckle import cli, stdin
import xml.etree.ElementTree as et

logging = logger.Logger()

# Singleton Runner
class Runner:
    instance = None
    is_running = False
    lock = None
    terminate = None
    is_vibing = False
    ai = None

    def __new__(self):
        if self.instance is None:
            self.instance = super().__new__(self)
            self.lock = threading.Lock()
            self.ai = ai.AI()
            self.exception_event = threading.Event()
            self.terminate = False
            self.is_vibing = False

        return self.instance

    def set_vibe(self, should_vibe):
        if should_vibe is True:
            self.ai.behavior(io.BytesIO(b.hcli_integration_behavior.encode('utf-8')))
        self.is_vibing = should_vibe

    def get_plan(self):
        self.ai.contextmgr.get_context()
        messages = self.ai.contextmgr.messages()
        if messages:
            # Get the last item using negative indexing
            last_message = messages[-1]
            if last_message['role'] == "assistant":
                # Parse with XML instead of regex
                try:
                    # Wrap in a root tag since XML needs a single root
                    plan_xml = f"<root>{last_message['content']}</root>"
                    root = et.fromstring(plan_xml)
                    plan_elem = root.find('.//plan')  # Find the first <plan> tag

                    if plan_elem is not None:
                        # Look for hcli tags within the plan
                        hcli_elems = plan_elem.findall('.//hcli')
                        if hcli_elems:
                            # Return the first hcli command
                            command = hcli_elems[0].text.strip() if hcli_elems[0].text else ""
                            logging.info(f"[ hai ] returning hcli integration: {command}")
                            return command
                        else:
                            logging.warning("[ hai ] Unable to vibe without a plan with hcli tags.")
                            return ""
                    else:
                        return ""
                except et.ParseError as e:
                    logging.warning(f"[ hai ] Failed to parse XML plan: {e}")
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
                    if dest == 'stdout':
                        stdout = stdout + chunk.decode()
                    elif dest == 'error':
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
