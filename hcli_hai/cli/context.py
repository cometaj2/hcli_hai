import json
import io
import os
import logger
import tiktoken
import config as c
import hutils
import summary as s
import resource
import threading

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
            return list(self._messages)  # Return a copy to prevent external modifications

    @messages.setter
    def messages(self, value):
        with self.rlock:
            self._messages = list(value)  # Make a copy to prevent external modifications

    def serialize(self):
        with self.rlock:
            # Create a clean dict without lock and private attributes
            clean_dict = {
                'title': self._title,
                'name': self._name,
                'messages': self._messages
            }

            return json.dumps(clean_dict, sort_keys=True, indent=4)


# class Context:
#     def __init__(self, model=None):
# 
#         self.title = ""
#         self.name = ""
#         self.messages = [{"role": "system", "content": ""}]
# 
#         # If model is provided, load it
#         if model is not None:
# 
#             # If model is a JSON string, parse it
#             if isinstance(model, str):
# 
#                 try:
#                     data = json.loads(model)
#                     self.__load_dict(data)
#                 except json.JSONDecodeError:
#                     logging.error("Invalid JSON string provided")
#                     raise ValueError("Invalid JSON string provided")
# 
#             # If model is already a dictionary
#             elif isinstance(model, dict):
#                 self.__load_dict(model)
# 
#             # If model is another object
#             else:
#                 for key, value in vars(model).items():
#                     setattr(self, key, value)
# 
#     def __load_dict(self, data):
#         for key, value in data.items():
#             setattr(self, key, value)
# 
#     def serialize(self):
#         return json.dumps(self, default=lambda o: o.__dict__,
#                          sort_keys=True,
#                          indent=4)

class ContextManager:
    init_lock = threading.Lock()
    instance = None

    def __new__(cls):
        with cls.init_lock:
            if cls.instance is None:
                cls.instance = super().__new__(cls)
                cls.instance.__init()
            return cls.instance

    def __init(self):
        self.rlock = threading.RLock()
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
            logging.debug(question)
            self.context.messages.append(question)

    def get_context(self):
        with self.rlock:
            self.context = self.config.get_context()
            return self.context

    # Ouput for human consumption and longstanding conversation tracking
    def get_readable_context(self):
        with self.rlock:
#             context_data = self.get_context()
            readable_context = []
            if self.context is not None:
                readable_context.append(f"----Name: {self.context.name}\n\n")
                readable_context.append(f"----Title: {self.context.title}\n\n")
                messages = self.context.messages
                for item in messages:
                    role = item.get('role', 'Unknown')
                    content = item.get('content', '')
                    readable_context.append(f"----{role.capitalize()}:\n\n{content}\n")

            result = "".join(readable_context)
            return result

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
