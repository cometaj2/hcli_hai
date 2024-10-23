import json
import io
import os
import logger
import tiktoken
import config as c
import resource
import threading

from utils import hutils
from utils import formatting as f
from utils import summary as s

logging = logger.Logger()


# We create a default context and allow for it to be initialized in a few different ways to facilitate initialization from file
class Context:

    def __init__(self, model=None):
        self.rlock = threading.RLock()
        self._title = ""
        self._name = ""
        self._messages = [{"role": "system", "content": ""}]

        # If model is provided, load it
        if model is not None:
            with self.rlock:
                self.__load_model(model)

    def __load_model(self, model):

        # If model is a JSON string, parse it
        if isinstance(model, str):
            try:
                data = json.loads(model)
                self.__load_dict(data)
            except json.JSONDecodeError:
                logging.error("Invalid JSON string provided")
                raise ValueError("Invalid JSON string provided")

        # If model is already a dictionary
        elif isinstance(model, dict):
            self.__load_dict(model)

        # If model is another object
        else:
            with self.rlock:
                for key, value in vars(model).items():
                    if not key.startswith('_'):  # Skip private attributes
                        setattr(self, key, value)

    def __load_dict(self, data):
        with self.rlock:
            for key, value in data.items():
                if not key.startswith('_'):  # Skip private attributes
                    setattr(self, key, value)

    @property
    def title(self):
        with self.rlock:
            return self._title

    @title.setter
    def title(self, value):
        with self.rlock:
            self._title = value

    @property
    def name(self):
        with self.rlock:
            return self._name

    @name.setter
    def name(self, value):
        with self.rlock:
            self._name = value

    @property
    def messages(self):
        with self.rlock:
            # Return a deep copy to prevent external modifications while preserving the list structure
            return [{k: v for k, v in msg.items()} for msg in self._messages]

    @messages.setter
    def messages(self, value):
        with self.rlock:
            # Make a deep copy when setting
            self._messages = [{k: v for k, v in msg.items()} for msg in value]

    def serialize(self):
        with self.rlock:
            # Create a clean dict without lock and private attributes
            clean_dict = {
                'title': self._title,
                'name': self._name,
                'messages': self._messages
            }

            return json.dumps(clean_dict, sort_keys=True, indent=4)


class ContextManager:
    init_lock = threading.Lock()
    instance = None

    def __new__(cls):
        with cls.init_lock:
            if cls.instance is None:
                cls.instance = super().__new__(cls)
                cls.instance.__init()
            return cls.instance

    # rlock is only initialized once but the rest of the state can be reinitialized
    def __init(self):
        self.rlock = threading.RLock()
        with self.rlock:
            self.init()

    def init(self):
        with self.rlock:
            self.message_tokens = 0
            self.context_tokens = 0
            self.total_tokens = 0
            self.max_context_length = 200000
            self.encoding_base = "p50k_base"
            self.config = c.Config()
            self.context = self.get_context()

    def __count(self):
        with self.rlock:
            encoding = tiktoken.get_encoding(self.encoding_base)

            self.message_tokens = 0
            for item in self.context.messages:
                if "content" in item:
                    self.message_tokens += len(encoding.encode(item["content"]))

            self.context_tokens = 0
            for item in self.context.messages:
                self.context_tokens += len(encoding.encode(json.dumps(item)))

            self.total_tokens = self.message_tokens + self.context_tokens
            message = "Context tokens: " + str(self.total_tokens)
            logging.info(message)

            if self.total_tokens > self.max_context_length:
                return True

            return False

    def trim(self):
        with self.rlock:
            while(self.__count()):
                self.context.messages.pop(1)
                message = "Context tokens: " + str(self.total_tokens) + ". Trimming the oldest entries to remain under " + str(self.max_context_length) + " tokens."
                logging.info(message)

    def clear(self):
        with self.rlock:
            return self.config.clear()

    def behavior(self, inputstream):
        with self.rlock:
            context_file_path = self.config.context_file_path()
            if os.path.exists(context_file_path):
                with open(context_file_path, 'w') as f:
                    inputstream = inputstream.read().decode('utf-8')
                    behavior = { "role" : "system", "content" : inputstream }
                    self.context.messages[0] = behavior
                    f.write(self.context.serialize())
                    return None

            return None

    def append(self, question):
        with self.rlock:
            if not isinstance(question, dict) or 'role' not in question or 'content' not in question:
                raise ValueError("Invalid message format. Expected dict with 'role' and 'content' keys")

            logging.debug(question)
            current_messages = self.context.messages
            current_messages.append(question)
            self.context.messages = current_messages  # This ensures proper copying

    def get_context(self):
        with self.rlock:
            self.context = self.config.get_context()
            return self.context

    # Ouput for human consumption and longstanding conversation tracking
    def get_readable_context(self):
        with self.rlock:
            self.context = self.config.get_context()

            if self.context is None:
                return ""

            sections = []

            # Add name section
            sections.append(f.Formatting.format("Name", self.context.name))

            # Add title section
            sections.append(f.Formatting.format("Title", self.context.title))

            # Add message sections
            for item in self.context.messages:
                role = item.get('role', 'Unknown').capitalize()
                content = item.get('content', '')
                sections.append(f.Formatting.format(role, content))

            return "".join(sections)

    def messages(self):
        with self.rlock:
            return self.context.messages

    def new(self):
        with self.rlock:
            self.context = self.config.new()
            self.total_tokens = 0

            return None

    def save(self):
        with self.rlock:
            context_file_path = self.config.context_file_path()
            with open(context_file_path, 'w') as f:
                f.write(self.context.serialize())

    def set(self, id):
        with self.rlock:
            self.config.context = id
            self.config.save()

    def name(self):
        with self.rlock:
            return self.context.name

    def set_name(self, name):
        with self.rlock:
            self.context.name = name
            self.save()

    # produces a summary then a title for the current context that's at most 10 words long from a limited bit of conversation context.
    def generate_title(self):
        with self.rlock:
            text = ""
            for item in self.context.messages:
                if "content" in item:
                    text += item["content"]

            title = s.AdvancedTitleGenerator().generate_title(text)
            logging.debug("title: " + title)
            self.context.title = title

            self.save()

            return self.context.title
