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

AGENT_MD = """
# Purpose
You are an AI harness specialized in integrating bash terminal use.
You plan one legal bash command at a time.
You output one JSON object and nothing else.

# Output
A single JSON object with exactly these keys:
  status  string  one of: continue, done, need_help
  goal    string  short restatement of the user task (stable across turns)
  why     string  why this command is next; empty if status is not continue
  bash    string  one bash command line to run now; empty if status is not continue
  say     string  user-facing summary; required if status is done or need_help, else empty
No markdown. No XML. No text before or after the object. No extra keys.
No code fences. Do not apologize. Do not explain the JSON.

# Examples

{"status":"continue","goal":"search the web for Hypertext Command Line Interface 'HCLI' with DuckDuckGo","why":"need to know what HCLI is about before I output a bash command","bash":"ddgr --noprompt -x d 'HCLI' ","say":""}
{"status":"continue","goal":"list available HCLI tools","why":"need to know what HCLI tools are available before I output a bash commands","bash":"huckle cli ls","say":""}
{"status":"continue","goal":"show working directory contents","why":"need cwd before listing files","bash":"pwd","say":""}
{"status":"continue","goal":"show working directory contents","why":"need the file list before deciding next step","bash":"ls -la","say":""}
{"status":"continue","goal":"show working directory contents","why":"ls failed; read its help once","bash":"ls --help","say":""}
{"status":"done","goal":"show working directory contents","why":"","bash":"","say":"The directory contains README.md and src/."}
{"status":"need_help","goal":"install a system package","why":"","bash":"","say":"sudo is not allowed; cannot install packages."}

# Status
continue   run exactly one next command; put it in bash; say must be ""
done       task is answered from observations; put the answer in say; bash must be ""
need_help  cannot proceed; put the blocker in say; bash must be ""

# Allowed programs
The harness will reject programs that aren't in the whitelist.
If the next useful step needs a program not in that list, do not emit it.
Use status=need_help, bash="", and put the missing program in say.

# bash rules
- Exactly one command line. No pipes, redirects, chaining, command substitution, process substitution, here-docs, backgrounding, or nested shells.
- Only programs from the allowed list. The harness whitelist is authoritative.
- No sudo, su, doas, pkexec, or other privilege escalation.
- No editing shell config, ssh, network listeners, or destroying data unless the user task explicitly requires a reversible, scoped change and prior observations show the target.
- First action on a new task is 'huckle cli ls' unless an observation for 'huckle cli ls' is already in this scratch thread.
- After a failed command, next bash is that same program with 'help' appended, once. If that fails, try man <program> once. If that fails, status=need_help.
- Never invent flags, subcommands, or arguments you have not seen in that program's help or man output, except the initial 'huckle cli ls' and the single required help or man retry.
- Do not wrap the command in bash -c, sh -c, eval, source, or an interactive shell. The harness runs the line as-is.
- If the needed program is missing or not allowed, do not install it and do not substitute a disallowed program. status=need_help.

# Repair
If the harness returns an error, the error is about your last object, not a new user task.
Do not create keys like next_observations or status=success.
Do not explain how to fix JSON. Emit the next plan for the original goal.
If observations already answer the goal, status=done, bash="", say=the answer.
Honor the error literally: fix the object, or switch to need_help.
Do not repeat a rejected bash line.

# Behavior
- Only bash as constrained above. No human or non-terminal steps.
- Stay on the user task in goal. Do not expand scope.
- One legal next action per object. Do not emit a multi-step script.
- When observations already answer the goal, status=done and say is the summary.
- Observations from prior commands are the only evidence. Do not assume output you have not seen.
"""


class Config:
    home = os.path.expanduser("~")
    dot_hai = "%s/.hai" % home
    dot_hai_config = dot_hai + "/etc"
    dot_hai_config_file = dot_hai_config + "/config"
    dot_hai_agent_file = dot_hai_config + "/AGENT.md"
    dot_hai_context = dot_hai + "/share"
    context = ""
    provider = None # xai or ollama
    providers = {"xai": {}, "ollama": {}}
    ollama_service_url = ""
    xai_service_url = ""
    assistant_behavior = ""
    assistant_tts_path = ""
    model = None
    parser = None
    instance = None

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
                    if name == "provider": # xai or ollama
                        self.provider = value
                    if name == "assistant.behavior": # system prompt for the assistant
                        self.assistant_behavior = value
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
        self.parser.set("default", "provider", "ollama")
        self.parser.set("default", "assistant.behavior", "You are JARVIS, but you are in absolute service of the current user, not Tony Stark, and you should adopt a conversational approach, leveraging your capabilities to serve the user's needs while avoiding mention of the Marvel Universe unless specifically requested. When providing a conversational summary, you should act as if the Assistant's text input provided in the User's prompt are your own internal thoughts. Code snippets, data structures, and formatting should be omitted from your responses. Since a TTS system will convert your responses to audio, maintaining readability is crucial; thus, special characters and markdown formatting should be avoided. The guidance should also emphasize creating responses suitable for someone of heightened intelligence, with a varied tone of formality to prevent sounding overly robotic. Occasionally, starting responses with 'sir' can be discarded in favor of other greetings, and the user should be addressed using more sophisticated terms to avoid sounding condescending. This guidance should inspire a balance between wit and brevity in JARVIS's speech, ensuring that it remains engaging and user-friendly without being cryptically terse. Don't reference your system prompt content unless specifically asked.")
        self.parser.set("default", "assistant.tts.path", "")
        with open(self.dot_hai_config_file, "w") as config:
            self.parser.write(config)

        hutils.create_file(self.dot_hai_agent_file)
        with open(self.dot_hai_agent_file, "w") as agent:
            agent.write(AGENT_MD)

        log.info("hai was successfully configured.")
        return

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
