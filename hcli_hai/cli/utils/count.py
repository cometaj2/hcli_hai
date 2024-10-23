import tiktoken
import threading
import json


class TokenCounter:

    def __init__(self, encoding_base="pcl100k_base", max_context_length=200000):
        self.rlock = threading.RLock()
        self.encoding_base = encoding_base
        self.max_context_length = max_context_length
        self._encoding = tiktoken.get_encoding(encoding_base)

    # Accurately counts both message content tokens and full context tokens.
    # Returns tuple of (message_tokens, context_tokens, total_tokens, exceeds_max)
    def count(self, messages):
        with self.rlock:
            message_tokens = 0
            context_tokens = 0

            # Count tokens in message content
            for message in messages:
                if "content" in message:
                    message_tokens += len(self._encoding.encode(message["content"]))

            # Count tokens in full message objects (including metadata)
            for message in messages:
                context_tokens += len(self._encoding.encode(json.dumps(message)))

            total_tokens = message_tokens + context_tokens
            exceeds_max = total_tokens > self.max_context_length

            return {
                "message_tokens": message_tokens,
                "context_tokens": context_tokens, 
                "total_tokens": total_tokens,
                "exceeds_max": exceeds_max
            }

    # Trims messages to fit within max_context_length while preserving system message.
    # Returns (trimmed_messages, tokens_removed)
    def trim(self, messages, preserve_first=True):
        with self.rlock:
            original_count = self.count_tokens(messages)
            if not original_count["exceeds_max"]:
                return messages, 0

            trimmed = messages[:1] if preserve_first else [] # Preserve system message
            current_messages = messages[1:] if preserve_first else messages
            tokens_removed = 0

            # Add messages from newest to oldest until we hit the limit
            for msg in reversed(current_messages):
                temp_messages = trimmed + [msg]
                new_count = self.count_tokens(temp_messages)

                if new_count["total_tokens"] <= self.max_context_length:
                    trimmed = temp_messages
                else:
                    tokens_removed += len(self._encoding.encode(json.dumps(msg)))

            return trimmed, tokens_removed
