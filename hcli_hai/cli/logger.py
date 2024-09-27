import logging
import traceback
import os
import datetime

DEBUG = logging.DEBUG
INFO = logging.INFO
WARNING = logging.WARNING
ERROR = logging.ERROR
CRITICAL = logging.CRITICAL

class Logger:
    instance = None

    def __new__(cls, *args, **kwargs):
        if cls.instance is None:

            cls.instance = super().__new__(cls, *args, **kwargs)
            cls.instance = logging.getLogger("crumbs")

            date_format = "%a %b %d %H:%M:%S %Y"
            message_format = "[%(asctime)s] %(levelname)s: [%(filename)s:%(lineno)s   ] %(message)s"

            formatter = logging.Formatter(fmt=message_format, datefmt=date_format)

            #now = datetime.datetime.now()
            #dirname = "./log"

            #if not os.path.isdir(dirname):
            #    os.mkdir(dirname)
            #fileHandler = logging.FileHandler(
            #    dirname + "/log_" + now.strftime("%Y-%m-%d")+".log")

            streamHandler = logging.StreamHandler()

            #fileHandler.setFormatter(formatter)
            streamHandler.setFormatter(formatter)

            #cls.instance.addHandler(fileHandler)
            cls.instance.addHandler(streamHandler)

        return cls.instance

    def setLevel(level):
        self.instance.setLevel(level)
