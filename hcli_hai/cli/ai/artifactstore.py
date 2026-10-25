import hashlib
import json
import mimetypes
import os
import re
import shutil
import tempfile
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

    def add_input_artifact(self, context_id, stream, filename=None):
        filename = self._safe_name(filename)
        input_dir = self._input_dir(context_id)

        temporary_path = None
        digest = hashlib.sha1()
        size = 0

        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=input_dir,
                prefix=".artifact-",
                delete=False,
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)

                while True:
                    chunk = stream.read(1024 * 1024)

                    if not chunk:
                        break

                    temporary_file.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)

            artifact_id = digest.hexdigest()
            artifact_dir = input_dir / artifact_id
            data_path = artifact_dir / filename
            metadata_path = artifact_dir / "metadata.json"

            try:
                artifact_dir.mkdir(parents=False, exist_ok=False)
            except FileExistsError:
                existing = self._find_input(context_id, artifact_id)

                if existing is not None:
                    return existing[0]["artifact_id"]

                raise

            os.replace(temporary_path, data_path)
            temporary_path = None

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

            return metadata["artifact_id"]
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink()
                except FileNotFoundError:
                    pass

    def _list(self, artifact_dir):
        artifacts = []

        for artifact_path in sorted(artifact_dir.iterdir()):
            if not artifact_path.is_dir():
                continue

            metadata_path = artifact_path / "metadata.json"

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

    def list_input(self, context_id):
        return self._list(self._input_dir(context_id))

    def list_output(self, context_id):
        return self._list(self._output_dir(context_id))

    def _find(self, artifact_dir, artifact_id):
        if not re.fullmatch(r"[0-9a-f]{40}", artifact_id or ""):
            return None

        artifact_path = artifact_dir / artifact_id

        if not artifact_path.is_dir():
            return None

        metadata_path = artifact_path / "metadata.json"

        if not metadata_path.is_file():
            return None

        try:
            metadata = json.loads(
                metadata_path.read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            return None

        if metadata.get("artifact_id") != artifact_id:
            return None

        filename = self._safe_name(metadata.get("filename"))

        if not filename or filename != metadata.get("filename"):
            return None

        data_path = artifact_path / filename

        if not data_path.is_file():
            return None

        return metadata, data_path

    def _find_input(self, context_id, artifact_id):
        return self._find(
            self._input_dir(context_id),
            artifact_id,
        )

    def _find_output(self, context_id, artifact_id):
        return self._find(
            self._output_dir(context_id),
            artifact_id,
        )

    def remove_input(self, context_id, artifact_id):
        artifact_dir = self._input_dir(context_id) / artifact_id

        if self._find_input(context_id, artifact_id) is None:
            return False

        shutil.rmtree(artifact_dir)
        return True

    def remove_output(self, context_id, artifact_id):
        artifact_dir = self._output_dir(context_id) / artifact_id

        if self._find_output(context_id, artifact_id) is None:
            return False

        shutil.rmtree(artifact_dir)
        return True

    def reset_input(self, context_id):
        input_dir = self._input_dir(context_id)

        for artifact_dir in input_dir.iterdir():
            if artifact_dir.is_dir():
                shutil.rmtree(artifact_dir)

        return True

    def reset_output(self, context_id):
        output_dir = self._output_dir(context_id)

        for artifact_dir in output_dir.iterdir():
            if artifact_dir.is_dir():
                shutil.rmtree(artifact_dir)

        return True

    def open_output(self, context_id, artifact_id):
        result = self._find_output(context_id, artifact_id)

        if result is None:
            return None

        metadata, data_path = result
        return metadata, data_path.open("rb")
