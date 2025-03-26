import json
import io
import service
import logger
from utils import formatting

from typing import Optional, Dict, Callable, List

logging = logger.Logger()
logging.setLevel(logger.INFO)


class CLI:
    def __init__(self, commands: List[str], inputstream: Optional[io.BytesIO] = None):
        self.commands = commands
        self.inputstream = inputstream
        self.service = service.Service()
        self.handlers: Dict[str, Callable] = {
            'clear': lambda: self.service.clear(),
            'context': self._handle_context,
            'ls':  self._handle_ls,
            'new': self._handle_new,
            'current': lambda: io.BytesIO(self.service.current().encode('utf-8')),
            'behavior': lambda: self.service.behavior(self.inputstream) if self.inputstream else None,
            'name': self._handle_name,
            'model': self._handle_model,
            'set': lambda: self.service.set(self.commands[2]) if len(self.commands) == 3 else None,
            'rm': lambda: self.service.rm(self.commands[2]) if len(self.commands) == 3 else None,
            'vibe': self._handle_vibe
        }

    def execute(self) -> Optional[io.BytesIO]:
        if len(self.commands) == 1 and self.inputstream:
            response = self.service.chat(self.inputstream)
            return io.BytesIO(response.encode('utf-8'))

        if len(self.commands) > 1 and self.commands[1] in self.handlers:
            return self.handlers[self.commands[1]]()

        return None

    def _handle_context(self) -> Optional[io.BytesIO]:
        if len(self.commands) == 2:
            readable_context = self.service.get_readable_context()
            return io.BytesIO(readable_context.encode('utf-8'))
        if len(self.commands) == 3 and self.commands[2] == '--json':
            context = self.service.get_context()
            return io.BytesIO(context.serialize().encode('utf-8'))

        return None

    def _handle_name(self) -> Optional[io.BytesIO]:
        if len(self.commands) == 2:
            name = self.service.name()
            return io.BytesIO((name or "None").encode('utf-8'))
        if len(self.commands) == 4 and self.commands[2] == "set":
            self.service.set_name(self.commands[3])

        return None

    def _handle_ls(self) -> Optional[io.BytesIO]:
        if len(self.commands) == 2:
            contexts = self.service.ls()
            return io.BytesIO(formatting.format_rows(contexts).encode('utf-8'))
        if len(self.commands) == 3:
            if self.commands[2] == "--json":
                return io.BytesIO(json.dumps(self.service.ls(), indent=4).encode('utf-8'))

        return None

    def _handle_new(self) -> Optional[io.BytesIO]:
        if len(self.commands) == 2:
            return io.BytesIO(self.service.new().encode('utf-8'))
        if len(self.commands) == 3:
            if self.commands[2] == "--json":
                return io.BytesIO(json.dumps([self.service.new()], indent=4).encode('utf-8'))

        return None

    def _handle_model(self) -> Optional[io.BytesIO]:
        if len(self.commands) == 2:
            model = self.service.model()
            return io.BytesIO((model or "None").encode('utf-8'))
        if len(self.commands) == 3:
            if self.commands[2] == "--json":
                model = self.service.model()
                return io.BytesIO(json.dumps([model or "None"], indent=4).encode('utf-8'))
            if self.commands[2] == "ls":
                models = self.service.list_models()
                return io.BytesIO("\n".join(models).encode('utf-8'))
        if len(self.commands) == 4:
            if self.commands[2] == "ls" and self.commands[3] == '--json':
                models = self.service.list_models()
                return io.BytesIO(json.dumps(models, indent=4).encode('utf-8'))
            if self.commands[2] == "set":
                self.service.set_model(self.commands[3])

        return None

    def _handle_vibe(self) -> None:
        if len(self.commands) == 3:
            if self.commands[2] == "start":
                self.service.vibe(True)
            elif self.commands[2] == "stop":
                self.service.vibe(False)
            elif self.commands[2] == "status":
                return io.BytesIO(self.service.status().encode('utf-8'))
        return None
