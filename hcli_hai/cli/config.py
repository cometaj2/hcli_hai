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
    ollama_service_url = ""
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
        else:
            log.critical("no available configuration.")
            sys.exit(1)

    # creates a configuration file for a named cli
    def create_configuration(self):
        hutils.create_file(self.dot_hai_config_file)

        self.parser.read_file(StringIO(u"[default]"))
        self.parser.set("default", "context", str(self.generate_id()))
        self.parser.set("default", "ollama.service.url", "http://127.0.0.1:11434")
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
