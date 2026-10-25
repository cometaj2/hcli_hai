import json
import mimetypes
import os
import re
import shutil
import uuid
import config as a

from pathlib import Path


class ArtifactStore:
    def __init__(self):
        self.config = a.Config()

    def _context_root(self, context_id):
        root = Path(self.config.dot_hai_context).resolve() / context_id
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _input_dir(self, context_id):
        path = self._context_root(context_id) / "artifacts" / "input"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _output_dir(self, context_id):
        path = self._context_root(context_id) / "artifacts" / "output"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _safe_name(self, name):
        name = os.path.basename(name or "artifact")
        name = re.sub(r"[^A-Za-z0-9._-]", "_", name)
        return name or "artifact"

    def add_input(self, context_id, stream, filename=None):
        artifact_id = uuid.uuid4().hex
        filename = self._safe_name(filename)

        artifact_dir = self._input_dir(context_id) / artifact_id
        artifact_dir.mkdir(parents=True, exist_ok=False)

        data_path = artifact_dir / filename
        metadata_path = artifact_dir / "metadata.json"

        size = 0

        with data_path.open("wb") as output:
            while True:
                chunk = stream.read(1024 * 1024)

                if not chunk:
                    break

                output.write(chunk)
                size += len(chunk)

        metadata = {
            "artifact_id": artifact_id,
            "filename": filename,
            "size": size,
            "mime_type": (
                mimetypes.guess_type(filename)[0]
                or "application/octet-stream"
            ),
            "kind": "input",
        }

        metadata_path.write_text(
            json.dumps(metadata, indent=2),
            encoding="utf-8",
        )

        return metadata

    def list_output(self, context_id):
        output_dir = self._output_dir(context_id)
        artifacts = []

        for artifact_dir in sorted(output_dir.iterdir()):
            if not artifact_dir.is_dir():
                continue

            metadata_path = artifact_dir / "metadata.json"

            if not metadata_path.is_file():
                continue

            try:
                metadata = json.loads(
                    metadata_path.read_text(encoding="utf-8")
                )
            except (OSError, json.JSONDecodeError):
                continue

            artifacts.append(metadata)

        return artifacts

    def _find_output(self, context_id, artifact_id):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", artifact_id or ""):
            return None

        artifact_dir = self._output_dir(context_id) / artifact_id

        if not artifact_dir.is_dir():
            return None

        metadata_path = artifact_dir / "metadata.json"

        if not metadata_path.is_file():
            return None

        metadata = json.loads(
            metadata_path.read_text(encoding="utf-8")
        )

        data_path = artifact_dir / metadata["filename"]

        if not data_path.is_file():
            return None

        return metadata, data_path

    def open_output(self, context_id, artifact_id):
        result = self._find_output(context_id, artifact_id)

        if result is None:
            return None

        metadata, data_path = result
        return metadata, data_path.open("rb")
