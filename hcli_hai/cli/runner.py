import io
import re
import logger
import threading
import time
import behavior as b
import ai
from huckle import cli, stdin

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

                # First check if there's a plan tag
                plan_pattern = r"<plan>(.*?)</plan>"
                plan_matches = re.findall(plan_pattern, last_message['content'], re.DOTALL)

                matches = []
                if plan_matches:
                    # Look for hcli tags within the first plan tag
                    pattern = r"<hcli>(.*?)</hcli>"
                    matches = re.findall(pattern, plan_matches[0], re.DOTALL)

                # Check if we have at least one match
                if matches:
                    # Only execute the first match regardless of how many there are
                    command = matches[0].strip()  # Extract the first command inside hcli tags
                    logging.info(f"[ hai ] returning hcli integration: {command}")
                    return command
                else:
                    logging.warning("[ hai ] I can't vibe without a plan and hcli tags.")
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
