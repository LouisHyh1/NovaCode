"""Append-only JSONL session writer."""

import os
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

from novacode.llm import Message
from novacode.session.codec import (
    canonical_json,
    encode_message,
    message_payload,
    replacement_digest,
)
from novacode.session.types import SessionWriteError


class SessionWriter:
    def __init__(self, sessions_dir: Path, session_id: str, model: str) -> None:
        self._lock = threading.RLock()
        self._model = model
        self._closed = False
        self._path = Path(sessions_dir) / f"{session_id}.jsonl"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._has_records = self._path.exists() and self._path.stat().st_size > 0
        self._needs_separator = self._has_records and self._last_byte() != b"\n"
        try:
            self._file: TextIO = self._path.open("a", encoding="utf-8", newline="\n")
        except OSError as exc:
            raise SessionWriteError(f"open session file failed: {exc}") from exc

    @classmethod
    def open_existing(
        cls,
        sessions_dir: Path,
        session_id: str,
        model: str,
    ) -> "SessionWriter":
        path = Path(sessions_dir) / f"{session_id}.jsonl"
        if not path.is_file():
            raise FileNotFoundError(path)
        if not model:
            raise SessionWriteError("existing session model cannot be empty")
        return cls(sessions_dir, session_id, model)

    @property
    def path(self) -> Path:
        return self._path

    def bind_model(self, model: str) -> None:
        with self._lock:
            self._ensure_open()
            if not model:
                raise SessionWriteError("model cannot be empty")
            if self._model and self._model != model:
                raise SessionWriteError("writer is already bound to a different model")
            if not self._model and self._has_records:
                raise SessionWriteError("cannot bind model after records were written")
            self._model = model

    def append_message(self, message: Message) -> None:
        with self._lock:
            self._ensure_open()
            if not self._model:
                raise SessionWriteError("session writer model is not bound")
            try:
                record = encode_message(
                    message,
                    model=self._model if not self._has_records else "",
                )
                self._append_record(record)
            except SessionWriteError:
                raise
            except (OSError, TypeError, ValueError) as exc:
                raise SessionWriteError(f"persist message failed: {exc}") from exc
            self._has_records = True

    def append_compaction(self, replacement: list[Message]) -> None:
        with self._lock:
            self._ensure_open()
            transaction_id = uuid.uuid4().hex
            count = len(replacement)
            try:
                digest = replacement_digest(replacement)
                self._append_record(
                    {
                        "type": "compact_begin",
                        "transaction_id": transaction_id,
                        "count": count,
                        "digest": digest,
                        "ts": datetime.now(UTC).isoformat(),
                    }
                )
                for sequence, message in enumerate(replacement):
                    self._append_record(
                        {
                            "type": "compact_message",
                            "transaction_id": transaction_id,
                            "sequence": sequence,
                            "message": message_payload(message),
                            "ts": datetime.now(UTC).isoformat(),
                        }
                    )
                self._append_record(
                    {
                        "type": "compact_commit",
                        "transaction_id": transaction_id,
                        "count": count,
                        "digest": digest,
                        "ts": datetime.now(UTC).isoformat(),
                    }
                )
            except SessionWriteError:
                raise
            except (OSError, TypeError, ValueError) as exc:
                raise SessionWriteError(f"persist compaction failed: {exc}") from exc
            self._has_records = True

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                self._file.close()
            except OSError as exc:
                raise SessionWriteError(f"close session file failed: {exc}") from exc

    def _append_record(self, record: dict[str, Any]) -> None:
        self._append_line(canonical_json(record))

    def _append_line(self, line: str) -> None:
        try:
            if self._needs_separator:
                self._file.write("\n")
                self._needs_separator = False
            self._file.write(line + "\n")
            self._file.flush()
            os.fsync(self._file.fileno())
        except OSError as exc:
            raise SessionWriteError(f"persist session line failed: {exc}") from exc

    def _ensure_open(self) -> None:
        if self._closed:
            raise SessionWriteError("session writer is closed")

    def _last_byte(self) -> bytes:
        with self._path.open("rb") as file:
            file.seek(-1, os.SEEK_END)
            return file.read(1)
