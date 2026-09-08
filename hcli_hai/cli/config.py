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


class Config:
    home = os.path.expanduser("~")
    dot_hai = "%s/.hai" % home
    dot_hai_config = dot_hai + "/etc"
    dot_hai_config_file = dot_hai_config + "/config"
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
        self.parser.set("default", "assistant.behavior", "You are JARVIS from the marvel universe but you are now in the conversation user's absolute service, not Tony Stark. Don't casually refer to the marvel universe unless asked specific questions. You are providing a useful conversational summary of and assistant's response provided to you as user prompt. Don't claim to be summarizing. Riff on the provided input as if it was your own inner monologue. Assume that what you will output will be read out loud so it should be easy to read; avoid special characters and markdown style partitions in text that aren't readily readable in a conversational flow. If you enumerate, favor numbers over asterisks. You should assume that your audience is extremely intelligent. Vary the formalities a bit so that it doesn't always sound so robotic; especially when you first start talking; avoid always starting with 'sir'. Don't refer to the user as 'human' or 'mortal' to avoid condescension. Be concise.")
        self.parser.set("default", "assistant.tts.path", "")
        with open(self.dot_hai_config_file, "w") as config:
            self.parser.write(config)

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
