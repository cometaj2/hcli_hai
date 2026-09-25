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
                log.info(f"vibe runner started.")
            else:
                log.info(f"vibe runner stopped.")

    def is_vibing(self):
        with self.rlock:
            return self._is_vibing

    def harness(self, command):
        self.is_running = True
        self.terminate = False

        try:
            log.info("attempting to vibe...")
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
