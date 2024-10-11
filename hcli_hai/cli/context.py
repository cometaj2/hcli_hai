import json
import io
import os
import logger
import tiktoken
import config as c
import hutils

import nltk
from sumy.parsers.plaintext import PlaintextParser
from sumy.nlp.tokenizers import Tokenizer
from sumy.summarizers.lsa import LsaSummarizer
from sumy.nlp.stemmers import Stemmer
from sumy.utils import get_stop_words

logging = logger.Logger()


class Context:
    title = None
    message_tokens = 0
    context_tokens = 0
    total_tokens = 0
    max_context_length = 10000
    encoding_base = "p50k_base"
    context = None
    context_file = None
    config = None
    instance = None

    def __new__(cls):
        if cls.instance is None:
            cls.instance = super().__new__(cls)
            cls.instance.init()
        return cls.instance

    def init(self):
        self.config = c.Config()
        self.context_file = self.config.dot_hai_context + "/" + self.config.context + "/context.json"
        self.get_context()

    def count(self):
        encoding = tiktoken.get_encoding(self.encoding_base)

        self.message_tokens = 0
        for item in self.context:
            if "content" in item:
                self.message_tokens += len(encoding.encode(item["content"]))

        self.context_tokens = 0
        for item in self.context:
            self.context_tokens += len(encoding.encode(json.dumps(item)))

        self.total_tokens = self.message_tokens + self.context_tokens
        message = "Context tokens: " + str(self.total_tokens)
        logging.info(message)

        if self.total_tokens > self.max_context_length:
            return True

        return False

    def trim(self):
        while(self.count()):
            self.context.pop(1)
            message = "Context tokens: " + str(self.total_tokens) + ". Trimming the oldest entries to remain under " + str(self.max_context_length) + " tokens."
            logging.info(message)

    def clear(self):
        if os.path.exists(self.context_file):
            os.remove(self.context_file)
            return self.new()

        return None

    def behavior(self, inputstream):
        if os.path.exists(self.context_file):
            f = open(self.context_file, "w")
            inputstream = inputstream.read().decode('utf-8')
            behavior = { "role" : "system", "content" : inputstream }
            self.context[0] = behavior
            json.dump(self.context, f)
            return None

        return None

    def append(self, question):
        logging.debug(question)
        self.context.append(question)

    def get_context(self):
        self.context_file = self.config.dot_hai_context + "/" + self.config.context + "/context.json"
        if os.path.exists(self.context_file):
            try:
                with open(self.context_file, 'r') as f:
                    titled_context = json.load(f)
                    self.title = titled_context.get('title', '')
                    self.context = titled_context.get('context', {})
                    return titled_context
            except:
                return self.new()
        else:
            return self.new()

        return None

    def messages(self):
        return self.context

    def new(self):
        share = self.config.dot_hai_context
        current_context = self.config.dot_hai_context + "/" + self.config.context

        if not os.path.exists(share):
            hutils.create_folder(share)

        if not os.path.exists(current_context):
            hutils.create_folder(current_context)

        if not os.path.exists(self.context_file):
            with open(self.context_file, 'w') as f:
                self.title = None
                self.context = [{ "role" : "system", "content" : "" }]
                titled_context = { 'title': self.title, 'context': self.context }
                json.dump(titled_context, f)
                return titled_context

        self.total_tokens = 0

        return None

    def save(self):
        titled_context = { 'title': self.title, 'context': self.context }

        with open(self.context_file, 'w') as f:
            json.dump(titled_context, f)

    def set(self, id):
        self.config.context = id
        self.config.save()
        self.context_file = self.config.dot_hai_context + "/" + self.config.context + "/context.json"

    def ensure_nltk_data(self):
        try:
            nltk.data.find('tokenizers/punkt')
        except LookupError:
            logging.debug("downloading required NLTK data...")
            nltk.download('punkt', quiet=True)
            nltk.download('punkt_tab', quiet=True)

    # produces a summary then a title for the current contextthat's at most 10 words long from a limited bit of conversation context.
    def generate_title(self):
        self.ensure_nltk_data()

        language = "english"
        sentences_count = 1

        text = ""
        for item in self.context:
            if "content" in item:
                text += item["content"]

        # Create a parser for the text
        parser = PlaintextParser.from_string(text, Tokenizer(language))

        # Initialize the LSA summarizer
        stemmer = Stemmer(language)
        summarizer = LsaSummarizer(stemmer)
        summarizer.stop_words = get_stop_words(language)

        # Generate the summary
        summary = summarizer(parser.document, sentences_count)

        # Join the sentences into a single string
        summary_text = " ".join(str(sentence) for sentence in summary)
        title = self.collapse(summary_text)

        logging.debug("title: " + title)
        self.title = title

        return title

    def collapse(self, text):
        max_words = 20

        words = nltk.word_tokenize(text)
        title_words = words[:max_words]
        title = " ".join(title_words)

        # If the last word is truncated, remove it
        if len(words) > max_words:
            title = " ".join(title_words[:-1])
            title += "..."

        return title
