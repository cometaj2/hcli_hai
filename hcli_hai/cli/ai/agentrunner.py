import logger
import threading
import traceback
import os
import re
import openai
import json
import config as a
from ai import ai
from ai.policy import STEP_KEYS, STEP_STATUSES, WHITELIST, bash_error, evidence_met
from pathlib import Path

log = logger.Logger()

MAX_REPAIRS = 5


class AgentRunner:
    """Execute one orchestrator task. Emits a single whitelisted command, or a terminal step.

    Does not own the user goal, the task list, or the client loop. The orchestrator calls
    step() and decides what the observation means for the rest of the plan.
    """

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
            self.lock = threading.RLock()
            self.is_running = False
            self.config = a.Config()
            self.initialized = True
            self.terminate = False
            self.agent_behavior = Path(self.config.dot_hai_agent_file).read_text(encoding="utf-8")
            self.ai = ai.AI()

    def __init_provider(self):
        with self.rlock:
            log.debug("initializing llm service provider")
            if self.config.provider == "ollama":
                self.client = openai.OpenAI(
                    base_url=self.config.ollama_service_url,
                    api_key="ollama",
                )
                log.debug(f"using ollama at {self.config.ollama_service_url}")

            elif self.config.provider == "xai":
                self.client = openai.OpenAI(
                    api_key=os.getenv("XAI_API_KEY"),
                    base_url="https://api.x.ai/v1",
                )
                log.debug("using grok (xai) at https://api.x.ai/v1")

            else:
                log.error("no provider selected. select from the list of available providers.")
                return False
            return True

    def __system_prompt(self):
        return (
            self.agent_behavior
            + "\n\n# Live constraints (runner-enforced)\n"
            + "You execute exactly one task. Do not plan the rest of the user goal.\n"
            + "Allowed programs: " + ", ".join(sorted(WHITELIST)) + "\n"
            + "The harness owns task completion. Do not decide that acceptance is met.\n"
            + "status=continue with one whitelisted bash and say=\"\", or status=need_help with bash=\"\" and say=the blocker.\n"
            + "status=done is rejected unless this task already has its own observation and no evidence check is pending.\n"
            + "goal must be exactly the task intent. Do not replace it with a broader goal.\n"
            + "Output one JSON object with keys: " + ", ".join(STEP_KEYS) + ".\n"
            + "No markdown. No text before or after the object.\n"
        )

    def __dump(self, step):
        return json.dumps({k: step.get(k, "") for k in STEP_KEYS}, ensure_ascii=False)

    def __parse_step(self, text):
        raw = (text or "").strip()
        if not raw:
            return None, "empty output; emit one json object with keys status,goal,why,bash,say"

        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
            raw = raw.strip()

        start, end = raw.find("{"), raw.rfind("}")
        if start == -1 or end <= start:
            return None, "no json object found; emit only one object with keys status,goal,why,bash,say"

        try:
            obj = json.loads(raw[start:end + 1])
        except ValueError as e:
            return None, "invalid json: %s" % e

        if not isinstance(obj, dict):
            return None, "top-level value must be a json object, not %s" % type(obj).__name__

        extra = set(obj) - set(STEP_KEYS)
        missing = [k for k in STEP_KEYS if k not in obj]
        if extra or missing:
            return None, "keys must be exactly %s; missing=%s extra=%s" % (
                list(STEP_KEYS), missing, sorted(extra)
            )

        if obj.get("status") not in STEP_STATUSES:
            return None, "status must be one of %s, got %r" % (list(STEP_STATUSES), obj.get("status"))

        for k in STEP_KEYS:
            if not isinstance(obj.get(k), str):
                return None, "%s must be a string" % k

        obj["bash"] = obj["bash"].strip()
        obj["say"] = obj["say"].strip()
        obj["goal"] = obj["goal"].strip()
        obj["why"] = obj["why"].strip()
        status = obj["status"]

        if status == "continue":
            if not obj["bash"]:
                return None, "status=continue requires a non-empty bash"
            if obj["say"]:
                return None, "status=continue requires say=\"\""
        else:
            if obj["bash"]:
                return None, "status=%s requires bash=\"\"" % status
            if not obj["say"]:
                return None, "status=%s requires a non-empty say" % status

        return obj, None

    def __scratch(self, task, observations, prior_keys):
        check = task.get("check") or {"kind": "open"}
        parts = [
            "task id: %s" % (task.get("id") or ""),
            "task intent: %s" % (task.get("intent") or ""),
            "acceptance: %s" % (task.get("acceptance") or "observations answer the task intent"),
            "evidence check: %s" % (
                "this task needs its own observation of %s" % (check.get("key") or check.get("bash") or "the implied command")
                if check.get("kind") == "observe"
                else "no single command is implied; gather evidence for this task only"
            ),
        ]
        hint = (task.get("hint") or "").strip()
        if hint:
            parts.append("hint: " + hint)
        if prior_keys:
            parts.append("already observed on other tasks (keys only, not evidence for this task):\n" + "\n".join(prior_keys))
        else:
            parts.append("no other-task keys yet.")

        if observations:
            blob = []
            for i, item in enumerate(observations, 1):
                result = item.get("result") or ""
                blob.append(
                    "this task observation %d\nbash: %s\nresult:\n%s"
                    % (i, item.get("bash", ""), result)
                )
            parts.append("\n\n".join(blob))
        else:
            parts.append("this task has no observations yet. status=done will be rejected.")

        parts.append(
            "The blocks above are evidence, not a format to copy.\n"
            "Only this task's observations count toward its acceptance.\n"
            "Do not repeat a key listed under already observed.\n"
            "Do not invent a path, URL, flag, or tool name that is not in the keys or in this task's observations.\n"
            "Emit one step object with keys status,goal,why,bash,say.\n"
            "goal must be exactly the task intent.\n"
            "Prefer status=continue with the command that satisfies the evidence check.\n"
            "Do not invent keys. Do not dump observations back as JSON.\n"
            "Do not assume output you have not been given."
        )
        return "\n\n".join(parts)

    def __complete(self, messages):
        model = self.config.model
        if self.config.provider is None or model is None:
            log.warning("no provider or model selected. select from the list of available providers and models.")
            return None

        if not self.__init_provider():
            return None

        kwargs = dict(model=model, messages=messages)
        try:
            response = self.client.chat.completions.create(
                response_format={"type": "json_object"},
                **kwargs
            )
        except Exception as e:
            log.debug("json response_format unsupported; retrying without it: %s" % e)
            response = self.client.chat.completions.create(**kwargs)

        log.debug(response)
        text = response.choices[0].message.content
        log.info(text)
        return text

    def step(self, task, observations, prior_keys=None):
        with self.lock:
            self.is_running = True
            self.terminate = False
            try:
                task = task or {}
                observations = list(observations or [])
                prior_keys = list(prior_keys or [])
                log.info("runner stepping task %s." % (task.get("id") or "?"))
                user = self.__scratch(task, observations, prior_keys)
                last = None
                critique = None
                intent = (task.get("intent") or "").strip()

                for attempt in range(MAX_REPAIRS):
                    if critique is None:
                        prompt = user
                    else:
                        log.warning("step rejected (%d/%d): %s" % (attempt, MAX_REPAIRS, critique))
                        prev = last if last and len(last) <= 800 else ((last or "")[:800] + "\n...")
                        prompt = (
                            user
                            + "\n\nYour previous output was rejected. It is not a new task.\n"
                            + "Do not describe JSON. Do not invent keys. Do not process the previous output.\n"
                            + "Stay on this task intent. Do not claim the task is done.\n"
                            + "error: %s\n" % critique
                            + "rejected output (do not copy its keys):\n%s\n\n" % prev
                            + "Emit exactly one of these shapes:\n"
                            + '{"status":"continue","goal":"%s","why":"","bash":"<one whitelisted command>","say":""}\n' % intent
                            + "or status=need_help with bash=\"\" and say=the blocker.\n"
                        )

                    last = self.__complete([
                        {"role": "system", "content": self.__system_prompt()},
                        {"role": "user", "content": prompt},
                    ])
                    if last is None:
                        return None

                    step, critique = self.__parse_step(last)
                    if step is None:
                        continue
                    step["goal"] = intent or step.get("goal") or ""

                    if step["status"] == "done":
                        if evidence_met(task):
                            critique = "harness closes this task; do not emit status=done"
                            continue
                        if (task.get("check") or {}).get("kind") == "observe":
                            critique = "evidence check is not met; emit the bash for it, or status=need_help"
                            continue
                        if not observations:
                            critique = "status=done rejected; this task has no observations"
                            continue

                    if step["status"] == "continue":
                        critique = bash_error(step["bash"])
                        if critique:
                            continue

                    log.info("runner accepted %s for task %s." % (step["status"], task.get("id") or "?"))
                    return step

                log.error("step repair exhausted: %s" % (critique or "unknown"))
                return {
                    "status": "need_help",
                    "goal": intent,
                    "why": "",
                    "bash": "",
                    "say": "could not produce a valid step: %s" % (critique or "unknown"),
                }
            except Exception:
                log.error(traceback.format_exc())
                return {
                    "status": "need_help",
                    "goal": (task or {}).get("intent") or "",
                    "why": "",
                    "bash": "",
                    "say": "runner failed before producing a step",
                }
            finally:
                self.terminate = False
                self.is_running = False
                log.info("runner idle.")

    def check_termination(self):
        if self.terminate:
            raise TerminationException("terminated")


class TerminationException(Exception):
    pass
