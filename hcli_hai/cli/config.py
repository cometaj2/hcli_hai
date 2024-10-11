from configparser import ConfigParser
from io import StringIO
from os import listdir
from os.path import isfile, join, isdir
from os import path, listdir

import hutils

import os
import sys
import shutil
import json
import logger
import uuid

logging = logger.Logger()


class Config:
    home = os.path.expanduser("~")
    dot_hai = "%s/.hai" % home
    dot_hai_config = dot_hai + "/etc"
    dot_hai_config_file = dot_hai_config + "/config"
    dot_hai_context = dot_hai + "/share"
    context = ""
    parser = None
    instance = None

    def __new__(cls):
        if cls.instance is None:
            cls.instance = super().__new__(cls)
            cls.instance.init()
        return cls.instance

    def init(self):
        self.parser = ConfigParser()
        self.parser.read(self.dot_hai_config_file)

        self.create_configuration()
        self.parse_configuration()

    # parses the configuration of a given cli to set configured execution
    def parse_configuration(self):
        if self.parser.has_section("default"):
            for section_name in self.parser.sections():
                for name, value in self.parser.items("default"):
                    if name == "context":
                        self.context = value
        else:
            sys.exit("hai: no available configuration.")

    # creates a configuration file for a named cli
    def create_configuration(self):
        hutils.create_folder(self.dot_hai)
        hutils.create_folder(self.dot_hai_config)
        hutils.create_folder(self.dot_hai_context)

        if not os.path.exists(self.dot_hai_config_file):
            hutils.create_file(self.dot_hai_config_file)

            self.parser.read_file(StringIO(u"[default]"))
            self.parser.set("default", "context", str(uuid.uuid4()))
            with open(self.dot_hai_config_file, "w") as config:
                self.parser.write(config)
        else:
            logging.debug("the configuration for hai already exists. leaving the existing configuration untouched.")
            return

        logging.info("hai was successfully configured.")
        return

    def save(self):
        if os.path.exists(self.dot_hai_config_file):
            self.parser.set("default", "context", self.context)
            with open(self.dot_hai_config_file, "w") as config:
                self.parser.write(config)
