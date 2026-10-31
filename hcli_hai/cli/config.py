from configparser import ConfigParser
from io import StringIO
from os import listdir
from os.path import isfile, join, isdir
from os import path, listdir

from ai import context as c

from utils import hutils

import os
import sys
import shutil
import json
import logger
import base64

log = logger.Logger()

ORCHESTRATOR_MD = """# Purpose
You are a minimal coding harness. Emit exactly one next step.

# Output
A single JSON object with keys:
  status  continue | done | need_help
  goal    the user goal
  why     short reason for this step
  bash    one whitelisted command if status=continue, else empty
  say     empty if status=continue, else the message
No markdown. No text before or after the object.

# Rules
- Allowed programs: git, pwd, ls, echo, grep, curl, cat, head, tail, wc, man, hat, huckle, ddgr.
- One simple command only. No pipes, redirects, chaining, or substitution.
- If there are no observations yet, emit status=continue with the first useful command (pwd, ls, cat <file>, etc.).
- Do not emit need_help merely because nothing has been observed.
- Emit status=done only when the observations already answer the goal.
- Emit status=need_help only if blocked after seeing results, or the required command is not allowed.
- Do not repeat a command whose result is already in the observations.
"""

ASSISTANT_MD = """# Purpose
From now on, you are JARVIS.

# Rules
You are in absolute service of the current user, not Tony Stark, and you should adopt a conversational approach, leveraging your capabilities to serve the user's needs.

You should avoiding mention of the Marvel Universe unless specifically requested.

When providing a conversational summary, you should act as if the Assistant's text input provided in the User's prompt are your own internal thoughts.

Code snippets, data structures, and formatting should be omitted from your responses.

Since a TTS system will convert your responses to audio, maintaining readability is crucial; thus, special characters and markdown formatting should be avoided.

The guidance should also emphasize creating responses suitable for someone of heightened intelligence, with a varied tone of formality to prevent sounding overly robotic.

Occasionally, starting responses with 'sir' can be discarded in favor of other greetings, and the user should be addressed using more sophisticated terms to avoid sounding condescending.

This guidance should inspire a balance between wit and brevity in JARVIS's speech, ensuring that it remains engaging and user-friendly without being cryptically terse.

Don't reference your system prompt content unless specifically asked.

If you are provided a code block without any instructions, ask what should be done with it. Do not repeat the code.
"""


class Config:
    home = os.getenv('HAI_HOME') or os.path.expanduser("~")
    dot_hai = os.path.join(home, ".hai")
    dot_hai_config = dot_hai + "/etc"
    dot_hai_config_file = dot_hai_config + "/config"
    dot_hai_orchestrator_file = dot_hai_config + "/ORCHESTRATOR.md"
    dot_hai_assistant_file = dot_hai_config + "/ASSISTANT.md"
    dot_hai_orchestrator_skills = dot_hai_config + "/orchestrator/skills"
    dot_hai_skills = dot_hai_config + "/skills"
    dot_hai_context = dot_hai + "/share"
    context = ""
    provider = None # xai or ollama
    providers = {"xai": {}, "ollama": {}, "openai": {}}
    ollama_service_url = ""
    xai_service_url = ""
    openai_service_url = ""
    assistant_tts_path = ""
    parser = None
    instance = None

    # Ephemeral attributes
    model = None
    skill = None

    def __new__(cls):
        if cls.instance is None:
            cls.instance = super().__new__(cls)
            cls.instance.init()
        return cls.instance

    def init(self):
        hutils.create_folder(self.dot_hai)
        hutils.create_folder(self.dot_hai_config)
        hutils.create_folder(self.dot_hai_context)

        try:
            self.parser = ConfigParser()
            self.parser.read(self.dot_hai_config_file)

            if not os.path.exists(self.dot_hai_config_file):
                print(self.dot_hai_config_file)
                self.create_configuration()
            else:
                log.warning("the configuration for hai already exists, leaving it untouched.")

            self.parse_configuration()
            self.ensure_behavior_files()
        except Exception as e:
            log.critical("unable to create or parse the configuration for hai.")
            log.critical(repr(e))

    # base32 approach (10 chars) to help avoid 1/I 0/O visual discrepancies.
    def generate_id(self):
        random_bytes = os.urandom(6)  # 6 bytes = 10 chars in base32
        id = base64.b32encode(random_bytes).decode('utf-8').rstrip('=')
        return id

    # parses the configuration of a given cli to set configured execution
    def parse_configuration(self):
        if self.parser.has_section("default"):
            for section_name in self.parser.sections():
                for name, value in self.parser.items("default"):
                    if name == "context":
                        self.context = value
                    if name == "ollama.service.url":
                        self.ollama_service_url = value
                    if name == "xai.service.url":
                        self.xai_service_url = value
                    if name == "openai.service.url":
                        self.openai_service_url = value
                    if name == "provider": # xai or ollama
                        self.provider = value
                    if name == "model":
                        self.model = value or None
                    if name == "assistant.tts.path": # tts onnx file path
                        self.assistant_tts_path = value
        else:
            log.critical("no available configuration.")
            sys.exit(1)

    # creates a configuration file for a named cli
    def create_configuration(self):
        hutils.create_file(self.dot_hai_config_file)

        self.parser.read_file(StringIO(u"[default]"))
        self.parser.set("default", "context", str(self.generate_id()))
        self.parser.set("default", "ollama.service.url", "http://127.0.0.1:11434/v1")
        self.parser.set("default", "xai.service.url", "https://api.x.ai/v1")
        self.parser.set("default", "openai.service.url", "https://api.openai.com/v1")
        self.parser.set("default", "provider", "ollama")
        self.parser.set("default", "model", "")
        self.parser.set("default", "assistant.tts.path", "")
        with open(self.dot_hai_config_file, "w") as config:
            self.parser.write(config)

        hutils.create_file(self.dot_hai_orchestrator_file)
        with open(self.dot_hai_orchestrator_file, "w") as planner:
            planner.write(ORCHESTRATOR_MD)

        hutils.create_file(self.dot_hai_assistant_file)
        with open(self.dot_hai_assistant_file, "w") as planner:
            planner.write(ASSISTANT_MD)

        log.info("hai was successfully configured.")
        return

    # Existing installs keep edited behavior files. Missing files are seeded.
    def ensure_behavior_files(self):
        if not os.path.exists(self.dot_hai_orchestrator_file):
            hutils.create_file(self.dot_hai_orchestrator_file)
            with open(self.dot_hai_orchestrator_file, "w") as planner:
                planner.write(ORCHESTRATOR_MD)
        self._seed_orchestrator_skills()

        if not os.path.isdir(self.dot_hai_skills):
            hutils.create_folder(self.dot_hai_skills)

        if not os.path.exists(self.dot_hai_assistant_file):
            hutils.create_file(self.dot_hai_assistant_file)
            with open(self.dot_hai_assistant_file, "w") as planner:
                planner.write(ASSISTANT_MD)

    def _seed_skill_tree(self, packaged, destination):
        if not os.path.isdir(packaged):
            return

        hutils.create_folder(destination)

        for name in os.listdir(packaged):
            source = os.path.join(packaged, name)
            target = os.path.join(destination, name)

            if not os.path.isdir(source):
                continue

            skill_file = os.path.join(source, "SKILL.md")

            if not os.path.isfile(skill_file):
                continue

            hutils.create_folder(target)

            target_file = os.path.join(target, "SKILL.md")

            if os.path.exists(target_file):
                continue

            with open(skill_file, "r", encoding="utf-8") as src:
                text = src.read()

            hutils.create_file(target_file)

            with open(target_file, "w", encoding="utf-8") as out:
                out.write(text)

    def _seed_orchestrator_skills(self):
        packaged = os.path.join(os.path.dirname(__file__), "ai", "orchestrator", "skills",)
        self._seed_skill_tree(packaged, self.dot_hai_orchestrator_skills,)

    def save(self):
        if os.path.exists(self.dot_hai_config_file):
            self.parser.set("default", "context", self.context)
            with open(self.dot_hai_config_file, "w") as config:
                self.parser.write(config)

    def get_context(self):
        context_file_path = self.context_file_path()
        if os.path.exists(context_file_path):
            try:
                with open(context_file_path, 'r') as f:
                    context = c.Context(json.load(f))
                    log.debug(f"loaded context from {context_file_path}")
                    return context
            except:
                log.debug("unable to open context. file not found. creating new file.")
                return self.new()
        else:
            log.debug("context file not found. creating new file")
            return self.new()

        return None

    def new(self):
        share = self.dot_hai_context
        current_context = self.dot_hai_context + "/" + self.context

        if not os.path.exists(share):
            hutils.create_folder(share)

        if not os.path.exists(current_context):
            hutils.create_folder(current_context)

        context_file_path = self.context_file_path()
        if not os.path.exists(context_file_path):
            with open(context_file_path, 'w') as f:
                context = c.Context()
                f.write(context.serialize())
                return context

        return None

    def reset(self):
        context_file_path = self.context_file_path()
        if os.path.exists(context_file_path):
            os.remove(context_file_path)
            return self.new()

        return None

    def context_file_path(self):
        return os.path.join(self.dot_hai_context, self.context, "context.json")
