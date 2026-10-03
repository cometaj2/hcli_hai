import json
import io
import os
import logger
import tiktoken
import config as c
import resource
import threading
import logger

from utils import hutils
from utils import formatting as f
from hcli_problem_details import *
from ai.policy import empty_step

log = logger.Logger()


def _empty_doc():
    return {
        "goal": "",
        "plan_status": "idle",
        "cursor": "",
        "tasks": [],
        "step": empty_step(),
        "say": "",
        "replans": 0,
    }


# Singleton plan. Ephemeral: not written into the saved conversation context.
# `hai agent plan` reads public(), which keeps status/goal/why/bash/say at the top level
# so an existing client can still run bash and post the observation to `hai agent next`.
class Plan:
    _instance = None
    _init_lock = threading.RLock()

    def __new__(cls):
        with cls._init_lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance.initialized = False
            return cls._instance

    def __init__(self):
        if self.initialized:
            return
        with self._init_lock:
            if self.initialized:
                return
            self.rlock = threading.RLock()
            self._doc = _empty_doc()
            self.initialized = True

    def clear(self):
        with self.rlock:
            self._doc = _empty_doc()

    def doc(self):
        with self.rlock:
            return json.loads(json.dumps(self._doc))

    def replace(self, doc):
        with self.rlock:
            self._doc = doc

    def public(self):
        with self.rlock:
            step = self._doc.get("step") or empty_step()
            status = step.get("status") or ""
            bash = (step.get("bash") or "") if status == "continue" else ""
            say = self._doc.get("say") or ""
            if not say:
                say = step.get("say") or ""
            if status == "continue":
                say = ""
            tasks = []
            for task in self._doc.get("tasks") or []:
                view = dict(task)
                obs = []
                for item in view.get("observations") or []:
                    result = item.get("result") or ""
                    if len(result) > 2000:
                        result = result[:2000] + "\n... (truncated)"
                    obs.append({"bash": item.get("bash") or "", "result": result})
                view["observations"] = obs
                tasks.append(view)
            return {
                "status": status,
                "goal": self._doc.get("goal") or "",
                "why": step.get("why") or "",
                "bash": bash,
                "say": say,
                "plan_status": self._doc.get("plan_status") or "idle",
                "cursor": self._doc.get("cursor") or "",
                "tasks": tasks,
                "step": {
                    "status": status,
                    "goal": step.get("goal") or "",
                    "why": step.get("why") or "",
                    "bash": bash,
                    "say": say,
                },
            }

    def dumps(self):
        with self.rlock:
            return json.dumps(self.public_unlocked(), ensure_ascii=False)

    def public_unlocked(self):
        step = self._doc.get("step") or empty_step()
        status = step.get("status") or ""
        bash = (step.get("bash") or "") if status == "continue" else ""
        say = self._doc.get("say") or ""
        if not say:
            say = step.get("say") or ""
        if status == "continue":
            say = ""
        tasks = []
        for task in self._doc.get("tasks") or []:
            view = dict(task)
            obs = []
            for item in view.get("observations") or []:
                result = item.get("result") or ""
                if len(result) > 2000:
                    result = result[:2000] + "\n... (truncated)"
                obs.append({"bash": item.get("bash") or "", "result": result})
            view["observations"] = obs
            tasks.append(view)
        return {
            "status": status,
            "goal": self._doc.get("goal") or "",
            "why": step.get("why") or "",
            "bash": bash,
            "say": say,
            "plan_status": self._doc.get("plan_status") or "idle",
            "cursor": self._doc.get("cursor") or "",
            "tasks": tasks,
            "step": {
                "status": status,
                "goal": step.get("goal") or "",
                "why": step.get("why") or "",
                "bash": bash,
                "say": say,
                "claimed": bool(step.get("claimed")),
                "id": step.get("id") or "",
            },
            "claimed": bool(step.get("claimed")),
        }

    @property
    def observations(self):
        with self.rlock:
            out = []
            for task in self._doc.get("tasks") or []:
                for item in task.get("observations") or []:
                    out.append({"bash": item.get("bash") or "", "result": item.get("result") or ""})
            return out

    def append_observation(self, bash, result):
        with self.rlock:
            cursor = self._doc.get("cursor") or ""
            tasks = self._doc.get("tasks") or []
            target = None
            for task in tasks:
                if task.get("id") == cursor:
                    target = task
                    break
            if target is None and tasks:
                target = tasks[-1]
            if target is None:
                return
            obs = target.setdefault("observations", [])
            bash = bash or ""
            for item in obs:
                if (item.get("bash") or "") == bash:
                    item["result"] = result or ""
                    return
            obs.append({"bash": bash, "result": result or ""})

    def has_observation(self, bash):
        wanted = (bash or "").strip()
        with self.rlock:
            cursor = self._doc.get("cursor") or ""
            for task in self._doc.get("tasks") or []:
                if task.get("id") != cursor:
                    continue
                for item in task.get("observations") or []:
                    if (item.get("bash") or "").strip() == wanted:
                        return True
        return False

    @property
    def plan(self):
        return self.dumps()

    @plan.setter
    def plan(self, value):
        with self.rlock:
            if not value:
                self._doc = _empty_doc()
                return
            try:
                obj = json.loads(value)
            except ValueError:
                self._doc = _empty_doc()
                self._doc["say"] = value
                return
            if not isinstance(obj, dict):
                self._doc = _empty_doc()
                return
            doc = _empty_doc()
            doc["goal"] = obj.get("goal") or ""
            doc["plan_status"] = obj.get("plan_status") or "active"
            doc["cursor"] = obj.get("cursor") or ""
            doc["tasks"] = obj.get("tasks") or []
            doc["say"] = obj.get("say") or ""
            step = obj.get("step") if isinstance(obj.get("step"), dict) else empty_step()
            if not step.get("status"):
                step = {
                    "status": obj.get("status") or "",
                    "goal": obj.get("goal") or "",
                    "why": obj.get("why") or "",
                    "bash": obj.get("bash") or "",
                    "say": obj.get("say") or "",
                }
            doc["step"] = step
            self._doc = doc

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
        if isinstance(model, str):
            try:
                data = json.loads(model)
                self.__load_dict(data)
            except json.JSONDecodeError:
                log.error("Invalid JSON string provided")
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
    _init_rlock = threading.RLock()
    _instance = None

    def __new__(cls):
        with cls._init_rlock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance.initialized = False
            return cls._instance

    # rlock is only initialized once but the rest of the state can be reinitialized
    def __init__(self):
        if self.initialized:
            return
        with self._init_rlock:
            if self.initialized:
                return
            self.rlock = threading.RLock()
            self.init()
            self.initialized = True

    # We get the context from disk when needed.
    # get context from the context manager return in memory context only.
    def init(self):
        with self.rlock:
            self.counter = TrimCounter()
            self.config = c.Config()
            self.context = self.config.get_context()
            self.plan = Plan()
            self.plan.clear()

    def trim(self):
        self.counter.trim(self.context)

    def reset(self):
        with self.rlock:
            reset = self.config.reset()
            self.init()
            return reset

    def behavior(self, inputstream):
        with self.rlock:
            inputstream = inputstream.read().decode('utf-8').rstrip()
            if inputstream != "":
                behavior = { "role" : "system", "content" : inputstream }

                current_messages = self.context.messages
                current_messages[0] = behavior
                self.context.messages = current_messages

                self.save()

                return None
            else:
                msg = "empty inputstream."
                log.error(msg)
                raise BadRequestError(detail=msg)

    def append(self, question):
        with self.rlock:
            if not isinstance(question, dict) or 'role' not in question or 'content' not in question:
                raise ValueError("Invalid message format. Expected dict with 'role' and 'content' keys")

            # Skip empty messages
            if question['content'].strip() == '':
                log.warning("Skipping attempt to add empty message")
                return

            log.debug(question)
            current_messages = self.context.messages
            current_messages.append(question)
            self.context.messages = current_messages  # This ensures proper copying

    def get_context(self):
        return self.context

    # Ouput for human consumption and longstanding conversation tracking
    def get_readable_context(self):
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

            return "".join(sections).rstrip()

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
            self.context = self.config.get_context()

    def name(self):
        with self.rlock:
            return self.context.name

    def set_name(self, name):
        with self.rlock:
            self.context.name = name
            self.save()

    def title(self):
        with self.rlock:
            return self.context.title

    def set_title(self, title):
        with self.rlock:
            self.context.title = title
            self.save()

    def provider(self):
        with self.rlock:
            return self.context.provider

    def set_provider(self, provider):
        with self.rlock:
            self.context.provider = provider
            self.save()

    def set_plan(self, plan):
        with self.rlock:
            self.plan.plan = plan

    def get_plan(self):
        with self.rlock:
            return self.plan.plan

    def get_step(self):
        with self.rlock:
            step = self.plan._doc.get("step") or empty_step()
            return json.dumps(step)

    def append_observation(self, bash, result):
        with self.rlock:
            self.plan.append_observation(bash, result)

    def observations(self):
        with self.rlock:
            return self.plan.observations

class TrimCounter:

    def __init__(self):
        self.encoding_base = "cl100k_base"
        self.max_context_length = 200000
        self.total_tokens = 0
        self._encoding = tiktoken.get_encoding(self.encoding_base)
        self._cached_encodings = {}

    def get_token_counts(self, messages):
        total_tokens = 0

        for message in messages:
            if "content" in message:
                content = message["content"]
                if content not in self._cached_encodings:
                    self._cached_encodings[content] = len(self._encoding.encode(content))
                total_tokens += self._cached_encodings[content]

        return {
            "total_tokens": total_tokens,
            "exceeds_max": total_tokens > self.max_context_length
        }

    def __count(self, messages):
        counts = self.get_token_counts(messages)
        self.total_tokens = counts["total_tokens"]

        if counts["exceeds_max"]:
            log.warning(f"Exceeding maximum context length by {self.total_tokens - self.max_context_length} tokens")

        return counts["exceeds_max"]

    def trim(self, context):
        while self.__count(context.messages):
            if len(context.messages) > 1:

                # Create new list without the second message (index 1)
                new_messages = [context.messages[0]] + context.messages[2:]
                context.messages = new_messages

                log.info(f"Context tokens: {self.total_tokens}. Trimming the oldest entries to remain under {self.max_context_length} tokens.")
            else:
                log.warning("Cannot trim further: only system message remains")
                break

        return context.messages

    def get_stats(self, context):
        return self.get_token_counts(context.messages)
