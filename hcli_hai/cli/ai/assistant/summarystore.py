import hashlib
import os
import re
import tempfile
import config as a

from datetime import datetime
from pathlib import Path


class SummaryStore:
    ID_PATTERN = re.compile(r"[0-9a-f]{40}")
    PREFIX_PATTERN = re.compile(r"[0-9a-f]{7}")

    def __init__(self):
        self.config = a.Config()

    def _context_root(self, context_id):
        context_id = str(context_id)

        # Prevent context_id from escaping the configured root.
        if not re.fullmatch(r"[A-Za-z0-9_-]+", context_id):
            raise ValueError("Invalid context_id")

        root = Path(self.config.dot_hai_context).resolve() / context_id
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _summaries_dir(self, context_id):
        path = self._context_root(context_id) / "summaries"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def add(self, context_id, stream):
        """
        Store a summary from a binary stream.

        Returns the full 40-character SHA-1 artifact ID. If the same
        summary already exists, the existing ID is returned.
        """
        summaries_dir = self._summaries_dir(context_id)

        temporary_path = None
        digest = hashlib.sha1()

        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=summaries_dir,
                prefix=".summary-",
                delete=False,
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)

                while True:
                    chunk = stream.read(1024 * 1024)

                    if not chunk:
                        break

                    if isinstance(chunk, str):
                        raise TypeError(
                            "Summary stream must be opened in binary mode"
                        )

                    temporary_file.write(chunk)
                    digest.update(chunk)

            summary_id = digest.hexdigest()
            summary_path = summaries_dir / summary_id

            # os.replace is safe if another writer stored the same content
            # concurrently, because both files have the same digest.
            os.replace(temporary_path, summary_path)
            temporary_path = None

            return summary_id

        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink()
                except FileNotFoundError:
                    pass

    def list_summaries(self, context_id):
        summaries_dir = self._summaries_dir(context_id)
        summaries = []

        for path in summaries_dir.iterdir():
            if not path.is_file():
                continue

            if not self.ID_PATTERN.fullmatch(path.name):
                continue

            try:
                update_time = path.stat().st_mtime

                with path.open("rb") as stream:
                    summary = stream.read().decode(
                        "utf-8",
                        errors="replace",
                    )

                summaries.append({
                    "summary_id": path.name,
                    "summary": summary.replace("\n", " "),
                    "update_time": update_time,
                })

            except OSError:
                continue

        summaries.sort(
            key=lambda summary: summary["update_time"],
            reverse=False,
        )

        for summary in summaries:
            summary["update_time"] = datetime.fromtimestamp(
                summary["update_time"]
            ).strftime("%Y-%m-%d %H:%M:%S")

        return summaries

    def _find(self, context_id, summary_id):
        """
        Resolve either a full 40-character ID or a unique 7-character
        prefix. Returns the summary path, or None if not found/ambiguous.
        """
        if summary_id is None:
            return None

        if isinstance(summary_id, bytes):
            summary_id = summary_id.decode("utf-8")
        else:
            summary_id = str(summary_id)

        summary_id = summary_id.strip()

        # Remove literal surrounding quotes, if present.
        if (
            len(summary_id) >= 2
            and summary_id[0] == summary_id[-1]
            and summary_id[0] in {"'", '"'}
        ):
            summary_id = summary_id[1:-1].strip()

        summary_id = summary_id.lower()

        summaries_dir = self._summaries_dir(context_id)

        if self.ID_PATTERN.fullmatch(summary_id):
            path = summaries_dir / summary_id

            if path.is_file():
                return path

            return None

        if self.PREFIX_PATTERN.fullmatch(summary_id):
            matches = [
                path
                for path in summaries_dir.iterdir()
                if (
                    path.is_file()
                    and self.ID_PATTERN.fullmatch(path.name)
                    and path.name.startswith(summary_id)
                )
            ]

            if len(matches) == 1:
                return matches[0]

        return None

    def open_summary(self, context_id, summary_id):
        """
        Open a summary for binary reading.

        Returns a file object, or None if the ID does not resolve to
        exactly one summary.
        """
        path = self._find(context_id, summary_id)

        if path is None:
            return None

        return path.open("rb")

    def read_summary(self, context_id, summary_id, max_bytes=None):
        """
        Read a summary as bytes.

        Returns None if the summary cannot be found.
        """
        path = self._find(context_id, summary_id)

        if path is None:
            return None

        with path.open("rb") as stream:
            if max_bytes is None:
                return stream.read()

            return stream.read(max_bytes)

    def read_summary_text(self, context_id, summary_id, max_bytes=50000):
        """
        Read a summary as UTF-8 text, replacing invalid byte sequences.
        """
        data = self.read_summary(context_id, summary_id, max_bytes=max_bytes + 1)

        if data is None:
            return None

        return data.decode("utf-8")

    def remove(self, context_id, summary_id):
        """
        Remove a summary by full ID or unique 7-character prefix.
        """
        path = self._find(context_id, summary_id)

        if path is None:
            return False

        path.unlink()
        return True

    def reset(self, context_id):
        """
        Remove all summaries for a context.
        """
        summaries_dir = self._summaries_dir(context_id)

        for path in summaries_dir.iterdir():
            if path.is_file():
                path.unlink()

        return True
