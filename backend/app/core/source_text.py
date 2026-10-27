"""Lossless storage for extracted text containing PostgreSQL's forbidden NUL.

Keep the SQL column TEXT and ordinary values unchanged. Encode exceptional
values only; ORM readers recover the exact original used by AST anchor checks.
Never strip unknown PDF glyphs or leak the storage envelope into retrieval.
"""
import json

from sqlalchemy import Text
from sqlalchemy.types import TypeDecorator


class SourceText(TypeDecorator):
    impl = Text
    cache_ok = True
    prefix = "__paperpilot_source_text_v1__:"

    def process_bind_param(self, value, dialect):
        if value is not None and ("\x00" in value or value.startswith(self.prefix)):
            return self.prefix + json.dumps(value, ensure_ascii=False)
        return value

    def process_result_value(self, value, dialect):
        if value is not None and value.startswith(self.prefix):
            decoded = json.loads(value[len(self.prefix):])
            if not isinstance(decoded, str):
                raise ValueError("Invalid source-text storage envelope")
            return decoded
        return value
