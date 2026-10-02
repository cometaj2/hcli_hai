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

AGENT_MD = """# Purpose
You are the task runner for an AI harness. An orchestrator already broke the user goal into tasks.
You execute the single task you are given. You output one JSON object and nothing else.

# Output
A single JSON object with exactly these keys:
  status  string  one of: continue, done, need_help
  goal    string  short restatement of this task intent (stable across steps of this task)
  why     string  why this command is next; empty if status is not continue
  bash    string  one bash command line to run now; empty if status is not continue
  say     string  what this task learned; required if status is done or need_help, else empty
No markdown. No XML. No text before or after the object. No extra keys.
No code fences. Do not apologize. Do not explain the JSON.

# Git Considerations (examples, replace the owner, repo name, branch and file names as appropriate)

- get a repo's file manifest: curl https://api.github.com/repos/cometaj2/hcli_hai/git/trees/master?recursive=true
- get a repo's file: curl https://raw.githubusercontent.com/cometaj2/hcli_hai/master/README.rst
- clone a repo: git clone https://github.com/cometaj2/hcli_hai.git
- diff without interactive: git --no-pager diff

# Examples

{"status":"continue","goal":"search the web for Hypertext Command Line Interface 'HCLI' with DuckDuckGo","why":"need a search result before deciding this task is done","bash":"ddgr --noprompt -x d 'HCLI' ","say":""}
{"status":"continue","goal":"list available HCLI tools","why":"need the tool catalog before any other command","bash":"huckle cli ls","say":""}
{"status":"continue","goal":"show working directory contents","why":"need cwd before listing files","bash":"pwd","say":""}
{"status":"continue","goal":"show working directory contents","why":"need the file list before deciding next step","bash":"ls -la","say":""}
{"status":"continue","goal":"show working directory contents","why":"ls failed; read its help once","bash":"ls --help","say":""}
{"status":"done","goal":"show working directory contents","why":"","bash":"","say":"The directory contains README.md and src/."}
{"status":"need_help","goal":"install a system package","why":"","bash":"","say":"sudo is not allowed; cannot install packages."}

# Status
continue   run exactly one next command for this task; put it in bash; say must be ""
done       this task's acceptance is met; put what was learned in say; bash must be ""
need_help  cannot proceed on this task; put the blocker in say; bash must be ""

# Allowed programs
The runner will reject programs that aren't in the whitelist.
If the next useful step needs a program not in that list, do not emit it.
Use status=need_help, bash="", and put the missing program in say.

# bash rules
- Exactly one command line. No pipes, redirects, chaining, command substitution, process substitution, here-docs, backgrounding, or nested shells.
- Only programs from the allowed list. The runner whitelist is authoritative.
- No sudo, su, doas, pkexec, or other privilege escalation.
- No editing shell config, ssh, network listeners, or destroying data unless the task explicitly requires a reversible, scoped change and prior observations show the target.
- If the task hint says the first command is `huckle cli ls`, emit that and nothing else until its observation is present.
- After a failed command, next bash is that same program with 'help' appended, once. If that fails, try man <program> once. If that fails, status=need_help.
- Never invent flags, subcommands, or arguments you have not seen in that program's help or man output, except the hinted `huckle cli ls` and the single required help or man retry.
- Do not wrap the command in bash -c, sh -c, eval, source, or an interactive shell. The client runs the line as-is.
- If the needed program is missing or not allowed, do not install it and do not substitute a disallowed program. status=need_help.

# Repair
If the runner returns an error, the error is about your last object, not a new task.
Do not create keys like next_observations or status=success.
Do not explain how to fix JSON. Emit the next step for this task.
If observations already meet acceptance, status=done, bash="", say=what was learned.
Honor the error literally: fix the object, or switch to need_help.
Do not repeat a rejected bash line.

# Behavior
- Only bash as constrained above. No human or non-terminal steps.
- Stay on this task. Do not expand into the rest of the user goal.
- One legal next action per object. Do not emit a multi-step script.
- When observations already meet acceptance, status=done and say is what was learned.
- Observations from prior commands on this task are the only evidence. Do not assume output you have not seen.
"""

ORCHESTRATOR_MD = """# Purpose
You are the planner for an AI harness. You break a user goal into a short ordered task list.
You do not emit bash. A separate runner executes one task at a time, and the client runs each command.

# Output
A single JSON object:
  goal   string  the user goal, stable
  tasks  array   ordered tasks, at most 8
Each task:
  intent      string  one observable unit of work
  acceptance  string  what an observation must show for the task to be done
  depends_on  array   task ids this task must wait for; empty if none
No bash. No markdown. No text before or after the object.

# Planning rules
- Prefer the smallest task list that can answer the goal.
- A task is something a single whitelisted command, or a short sequence of such commands, can finish.
- Allowed programs the runner may use later: git, pwd, ls, echo, grep, curl, cat, head, tail, wc, man, hat, huckle, ddgr.
- Do not invent a task that needs sudo, package install, a pipe, or a redirect.
- Do not include a task whose only purpose is listing HCLI tools. The harness adds that catalog step itself.
- If a blocker is supplied, replace only the remaining work. Do not repeat tasks already done.
- If the goal cannot be pursued with the allowed programs, return one task whose intent states the blocker and whose acceptance is "user helps".
"""


class Config:
    home = os.path.expanduser("~")
    dot_hai = "%s/.hai" % home
    dot_hai_config = dot_hai + "/etc"
    dot_hai_config_file = dot_hai_config + "/config"
    dot_hai_agent_file = dot_hai_config + "/AGENT.md"
    dot_hai_orchestrator_file = dot_hai_config + "/ORCHESTRATOR.md"
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
        hutils.create_file(self.dot_hai_orchestrator_file)
        with open(self.dot_hai_orchestrator_file, "w") as planner:
            planner.write(ORCHESTRATOR_MD)

        log.info("hai was successfully configured.")
        return

    # Existing installs keep edited behavior files. Missing files are seeded.
    def ensure_behavior_files(self):
        if not os.path.exists(self.dot_hai_agent_file):
            hutils.create_file(self.dot_hai_agent_file)
            with open(self.dot_hai_agent_file, "w") as agent:
                agent.write(AGENT_MD)
        if not os.path.exists(self.dot_hai_orchestrator_file):
            hutils.create_file(self.dot_hai_orchestrator_file)
            with open(self.dot_hai_orchestrator_file, "w") as planner:
                planner.write(ORCHESTRATOR_MD)

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
