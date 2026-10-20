import hashlib
import json
import os
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path


_SECRET_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key\s*[=:]\s*)[^\s,]+"),
    re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[^\s]+"),
    re.compile(r"(?i)(token\s*[=:]\s*)[^\s,]+"),
    re.compile(r"(?i)(password\s*[=:]\s*)[^\s,]+"),
)


def local_now():
    """
    Return the current local time as an ISO 8601 timestamp including the
    local UTC offset.
    """
    return datetime.now().astimezone().isoformat()


def redact(value):
    """
    Best-effort redaction before trace persistence.

    Trace fields such as goals, commands, and command results are redacted.
    Context snapshots are not redacted because they must preserve the exact
    conversation state.
    """
    if value is None:
        return value

    if not isinstance(value, str):
        value = str(value)

    for pattern in _SECRET_PATTERNS:
        value = pattern.sub(r"\1[REDACTED]", value)

    return value


class RunStore:
    """
    Durable append-only execution trace and context snapshot store.

    Layout:

        <context_folder>/
            context.json
            runs/
                <run_id>/
                    trace.jsonl
                    context.before.json
                    context.after.json

    Every trace event is written as one JSONL record. Context snapshots are
    complete state files associated with the run.
    """

    _instance = None
    _instance_lock = threading.RLock()

    def __new__(cls):
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance.initialized = False

            return cls._instance

    def __init__(self):
        if self.initialized:
            return

        with self._instance_lock:
            if self.initialized:
                return

            self.lock = threading.RLock()
            self.initialized = True

    # ------------------------------------------------------------------
    # Path handling
    # ------------------------------------------------------------------

    def _context_folder(self, config, context_id):
        context_id = str(context_id or "").strip()

        if not context_id:
            raise ValueError("context_id is required")

        # Context IDs are generated internally, but validate them before
        # constructing filesystem paths.
        if not re.fullmatch(r"[A-Za-z0-9_-]+", context_id):
            raise ValueError("invalid context_id")

        return Path(config.dot_hai_context) / context_id

    def _run_folder(self, config, context_id, run_id):
        run_id = str(run_id or "").strip()

        if not re.fullmatch(r"[A-Za-z0-9_-]+", run_id):
            raise ValueError("invalid run_id")

        context_folder = self._context_folder(
            config,
            context_id,
        )

        runs_folder = (context_folder / "runs").resolve()
        run_folder = (runs_folder / run_id).resolve()

        # Ensure the resolved run path remains inside the context's runs
        # directory.
        run_folder.relative_to(runs_folder)

        return run_folder

    def _trace_path(self, config, context_id, run_id):
        return (
            self._run_folder(
                config,
                context_id,
                run_id,
            )
            / "trace.jsonl"
        )

    def _snapshot_path(
        self,
        config,
        context_id,
        run_id,
        phase,
    ):
        if phase not in ("before", "after"):
            raise ValueError("phase must be 'before' or 'after'")

        return (
            self._run_folder(
                config,
                context_id,
                run_id,
            )
            / "context.%s.json" % phase
        )

    # ------------------------------------------------------------------
    # Run creation and event persistence
    # ------------------------------------------------------------------

    def create_run(
        self,
        config,
        context_id,
        goal,
        originating_message_index=None,
        originating_message_sha256=None,
    ):
        """
        Create a new unique run directory and write the first event.
        """
        with self.lock:
            context_folder = self._context_folder(
                config,
                context_id,
            )

            runs_folder = context_folder / "runs"

            context_folder.mkdir(
                parents=True,
                exist_ok=True,
            )

            runs_folder.mkdir(
                parents=True,
                exist_ok=True,
            )

            run_id = uuid.uuid4().hex

            run_folder = self._run_folder(
                config,
                context_id,
                run_id,
            )

            run_folder.mkdir(
                mode=0o700,
                parents=False,
                exist_ok=False,
            )

            os.chmod(run_folder, 0o700)

            trace_path = run_folder / "trace.jsonl"
            trace_path.touch(
                mode=0o600,
                exist_ok=False,
            )

            os.chmod(trace_path, 0o600)

            event = {
                "event": "run_started",
                "run_id": run_id,
                "context_id": context_id,
                "goal": redact(goal),
                "originating_message_index": (
                    originating_message_index
                ),
                "originating_message_sha256": (
                    originating_message_sha256
                ),
                "created_at": local_now(),
            }

            self._append_path(
                trace_path,
                event,
            )

            return run_id

    def append(
        self,
        config,
        context_id,
        run_id,
        event,
        event_type=None,
    ):
        """
        Append exactly one event as one JSON line.

        Existing lines are never modified.
        """
        with self.lock:
            trace_path = self._trace_path(
                config,
                context_id,
                run_id,
            )

            if not trace_path.exists():
                raise FileNotFoundError(
                    "execution trace does not exist: %s"
                    % trace_path
                )

            clean_event = dict(event or {})

            clean_event["run_id"] = run_id
            clean_event["context_id"] = context_id
            clean_event["event"] = (
                event_type
                or clean_event.get("event")
                or "event"
            )
            clean_event["recorded_at"] = local_now()

            for field in (
                "bash",
                "result",
                "goal",
                "summary",
            ):
                if field in clean_event:
                    clean_event[field] = redact(
                        clean_event[field]
                    )

            self._append_path(
                trace_path,
                clean_event,
            )

    def finish(
        self,
        config,
        context_id,
        run_id,
        status,
        summary="",
        response_message_index=None,
    ):
        """
        Append a basic run_finished event.

        Snapshot-aware callers should generally use append() directly so
        context.after.json metadata can be included in the final event.
        """
        self.append(
            config=config,
            context_id=context_id,
            run_id=run_id,
            event_type="run_finished",
            event={
                "status": status,
                "summary": redact(summary),
                "response_message_index": (
                    response_message_index
                ),
                "completed_at": local_now(),
            },
        )

    # ------------------------------------------------------------------
    # Context snapshots
    # ------------------------------------------------------------------

    def snapshot_context(
        self,
        config,
        context_id,
        run_id,
        context,
        filename,
    ):
        """
        Atomically write a complete context snapshot into the run directory.

        Snapshots are intentionally not redacted. They represent exact
        conversation state and may later be inspected or used by future
        restoration/forking functionality.
        """
        filename = str(filename or "").strip()

        if filename not in (
            "context.before.json",
            "context.after.json",
        ):
            raise ValueError(
                "invalid context snapshot filename"
            )

        with self.lock:
            run_folder = self._run_folder(
                config,
                context_id,
                run_id,
            )

            destination = run_folder / filename
            temporary = run_folder / (
                ".%s.%s.tmp"
                % (
                    filename,
                    uuid.uuid4().hex,
                )
            )

            if hasattr(context, "serialize"):
                serialized = context.serialize()
            elif isinstance(context, str):
                serialized = context
            else:
                serialized = json.dumps(
                    context,
                    ensure_ascii=False,
                    sort_keys=True,
                    indent=4,
                )

            if not serialized.endswith("\n"):
                serialized += "\n"

            try:
                with temporary.open(
                    "w",
                    encoding="utf-8",
                ) as stream:
                    stream.write(serialized)
                    stream.flush()
                    os.fsync(stream.fileno())

                os.chmod(temporary, 0o600)

                # Atomic replacement prevents readers from observing a
                # partially-written snapshot.
                os.replace(
                    temporary,
                    destination,
                )

                os.chmod(destination, 0o600)

            finally:
                if temporary.exists():
                    try:
                        temporary.unlink()
                    except OSError:
                        pass

            return {
                "path": filename,
                "sha256": self._sha256_file(destination),
                "bytes": destination.stat().st_size,
            }

    def snapshot_before(
        self,
        config,
        context_id,
        run_id,
        context,
    ):
        """
        Capture the exact context state from which the run began.
        """
        metadata = self.snapshot_context(
            config=config,
            context_id=context_id,
            run_id=run_id,
            context=context,
            filename="context.before.json",
        )

        self.append(
            config=config,
            context_id=context_id,
            run_id=run_id,
            event_type="context_before_captured",
            event={
                "snapshot": metadata,
            },
        )

        return metadata

    def snapshot_after(
        self,
        config,
        context_id,
        run_id,
        context,
    ):
        """
        Capture the context state after the final assistant response has been
        committed.
        """
        metadata = self.snapshot_context(
            config=config,
            context_id=context_id,
            run_id=run_id,
            context=context,
            filename="context.after.json",
        )

        self.append(
            config=config,
            context_id=context_id,
            run_id=run_id,
            event_type="context_after_captured",
            event={
                "snapshot": metadata,
            },
        )

        return metadata

    def read_snapshot(
        self,
        config,
        context_id,
        run_id,
        phase="before",
    ):
        """
        Read a before or after context snapshot.

        Restoration and forking are intentionally not implemented here.
        """
        snapshot_path = self._snapshot_path(
            config=config,
            context_id=context_id,
            run_id=run_id,
            phase=phase,
        )

        with snapshot_path.open(
            "r",
            encoding="utf-8",
        ) as stream:
            return json.load(stream)

    # ------------------------------------------------------------------
    # Trace retrieval
    # ------------------------------------------------------------------

    def read(
        self,
        config,
        context_id,
        run_id,
    ):
        """
        Read a trace without modifying it.
        """
        with self.lock:
            trace_path = self._trace_path(
                config,
                context_id,
                run_id,
            )

            with trace_path.open(
                "r",
                encoding="utf-8",
            ) as stream:
                return [
                    json.loads(line)
                    for line in stream
                    if line.strip()
                ]

    def read_jsonl(
        self,
        config,
        context_id,
        run_id,
    ):
        """
        Return the raw JSONL representation for CLI/API output.
        """
        with self.lock:
            trace_path = self._trace_path(
                config,
                context_id,
                run_id,
            )

            return trace_path.read_text(
                encoding="utf-8",
            )

    def list_runs(
        self,
        config,
        context_id,
    ):
        """
        List run IDs for a context without changing any trace.
        """
        with self.lock:
            context_folder = self._context_folder(
                config,
                context_id,
            )

            runs_folder = context_folder / "runs"

            if not runs_folder.exists():
                return []

            return sorted(
                item.name
                for item in runs_folder.iterdir()
                if (
                    item.is_dir()
                    and re.fullmatch(
                        r"[A-Za-z0-9_-]+",
                        item.name,
                    )
                    and (item / "trace.jsonl").is_file()
                )
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def message_sha256(content):
        """
        Stable reference to the originating user request without changing
        the existing context message schema.
        """
        return hashlib.sha256(
            (content or "").encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _sha256_file(path):
        digest = hashlib.sha256()

        with open(path, "rb") as stream:
            for chunk in iter(
                lambda: stream.read(1024 * 1024),
                b"",
            ):
                digest.update(chunk)

        return digest.hexdigest()

    @staticmethod
    def _append_path(path, event):
        serialized = json.dumps(
            event,
            ensure_ascii=False,
            separators=(",", ":"),
        )

        with path.open(
            "a",
            encoding="utf-8",
        ) as stream:
            stream.write(serialized + "\n")
            stream.flush()
            os.fsync(stream.fileno())
