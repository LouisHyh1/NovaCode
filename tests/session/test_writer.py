import json
import os
import threading
from pathlib import Path

import pytest

from novacode.llm import Message, ToolCall, ToolResult
from novacode.session import SessionWriteError, SessionWriter, load_session

SESSION_ID = "20260720-090807-abcd"


def _read_records(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_writer_appends_complete_message_records_and_first_model(tmp_path: Path) -> None:
    writer = SessionWriter(tmp_path, SESSION_ID, "test-model")
    writer.append_message(Message(role="user", content="hello"))
    writer.append_message(
        Message(
            role="assistant",
            content="reading",
            tool_calls=[ToolCall(id="t1", name="read_file", input='{"path":"a.txt"}')],
        )
    )
    writer.append_message(
        Message(
            role="tool",
            tool_results=[
                ToolResult(
                    tool_call_id="t1",
                    content="result",
                    is_error=True,
                    is_policy_denial=True,
                )
            ],
        )
    )
    writer.close()

    records = _read_records(tmp_path / f"{SESSION_ID}.jsonl")
    assert [record["role"] for record in records] == ["user", "assistant", "tool"]
    assert records[0]["model"] == "test-model"
    assert "model" not in records[1]
    assert records[1]["tool_calls"] == [
        {"id": "t1", "name": "read_file", "input": '{"path":"a.txt"}'}
    ]
    assert records[2]["tool_results"] == [
        {
            "tool_call_id": "t1",
            "content": "result",
            "is_error": True,
            "is_policy_denial": True,
        }
    ]
    assert all(
        record["type"] == "message" and record["ts"].endswith("+00:00") for record in records
    )


def test_writer_requires_model_binding_and_rejects_conflicts(tmp_path: Path) -> None:
    writer = SessionWriter(tmp_path, SESSION_ID, "")

    with pytest.raises(SessionWriteError, match="model"):
        writer.append_message(Message(role="user", content="blocked"))

    writer.bind_model("model-a")
    writer.bind_model("model-a")
    writer.append_message(Message(role="user", content="accepted"))

    with pytest.raises(SessionWriteError, match="different model"):
        writer.bind_model("model-b")
    writer.close()


def test_bind_model_rejects_empty_or_late_first_binding(tmp_path: Path) -> None:
    writer = SessionWriter(tmp_path, SESSION_ID, "model-a")
    with pytest.raises(SessionWriteError, match="empty"):
        writer.bind_model("")
    writer.append_message(Message(role="user", content="first"))
    writer._model = ""
    with pytest.raises(SessionWriteError, match="after records"):
        writer.bind_model("model-a")
    writer.close()


def test_open_existing_keeps_original_file_and_model(tmp_path: Path) -> None:
    first = SessionWriter(tmp_path, SESSION_ID, "model-a")
    first.append_message(Message(role="user", content="first"))
    first.close()

    reopened = SessionWriter.open_existing(tmp_path, SESSION_ID, "model-a")
    reopened.append_message(Message(role="assistant", content="second"))
    reopened.close()

    records = _read_records(tmp_path / f"{SESSION_ID}.jsonl")
    assert [record["content"] for record in records] == ["first", "second"]
    assert [record.get("model") for record in records] == ["model-a", None]


def test_writer_flushes_and_fsyncs_each_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []
    monkeypatch.setattr(os, "fsync", calls.append)
    writer = SessionWriter(tmp_path, SESSION_ID, "model-a")

    writer.append_message(Message(role="user", content="one"))
    writer.append_message(Message(role="assistant", content="two"))
    writer.close()

    assert len(calls) == 2


@pytest.mark.parametrize("failure", ["write", "flush", "fsync"])
def test_writer_wraps_persistence_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    writer = SessionWriter(tmp_path, SESSION_ID, "model-a")

    if failure == "fsync":
        monkeypatch.setattr(os, "fsync", lambda _: (_ for _ in ()).throw(OSError("fsync")))
    else:
        original = writer._file

        class FailingFile:
            def write(self, data: str) -> int:
                if failure == "write":
                    raise OSError("write")
                return original.write(data)

            def flush(self) -> None:
                if failure == "flush":
                    raise OSError("flush")
                original.flush()

            def fileno(self) -> int:
                return original.fileno()

            def close(self) -> None:
                original.close()

        writer._file = FailingFile()

    with pytest.raises(SessionWriteError, match=failure):
        writer.append_message(Message(role="user", content="hello"))
    writer.close()


def test_writer_serializes_concurrent_appends_as_valid_lines(tmp_path: Path) -> None:
    writer = SessionWriter(tmp_path, SESSION_ID, "model-a")
    threads = [
        threading.Thread(
            target=writer.append_message,
            args=(Message(role="user", content=str(index)),),
        )
        for index in range(20)
    ]

    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    writer.close()

    records = _read_records(tmp_path / f"{SESSION_ID}.jsonl")
    assert len(records) == 20
    assert {record["content"] for record in records} == {str(index) for index in range(20)}


def test_writer_close_is_idempotent_and_blocks_later_append(tmp_path: Path) -> None:
    writer = SessionWriter(tmp_path, SESSION_ID, "model-a")
    writer.close()
    writer.close()

    with pytest.raises(SessionWriteError, match="closed"):
        writer.append_message(Message(role="user", content="late"))


def test_committed_compaction_replaces_prior_history(tmp_path: Path) -> None:
    writer = SessionWriter(tmp_path, SESSION_ID, "model-a")
    writer.append_message(Message(role="user", content="old"))
    writer.append_compaction(
        [
            Message(role="user", content="summary"),
            Message(role="assistant", content="tail"),
        ]
    )
    writer.close()

    result = load_session(tmp_path / f"{SESSION_ID}.jsonl")
    result_path = writer.path

    assert [message.content for message in result.messages] == ["summary", "tail"]
    assert [record["type"] for record in _read_records(result_path)] == [
        "message",
        "compact_begin",
        "compact_message",
        "compact_message",
        "compact_commit",
    ]
    assert result_path == tmp_path / f"{SESSION_ID}.jsonl"


@pytest.mark.parametrize("fail_after", [1, 2, 3])
def test_incomplete_compaction_is_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fail_after: int
) -> None:
    writer = SessionWriter(tmp_path, SESSION_ID, "model-a")
    writer.append_message(Message(role="user", content="old"))
    original_append = writer._append_line
    calls = 0

    def interrupted(line: str) -> None:
        nonlocal calls
        calls += 1
        if calls > fail_after:
            raise OSError("interrupted")
        original_append(line)

    monkeypatch.setattr(writer, "_append_line", interrupted)
    with pytest.raises(SessionWriteError, match="interrupted"):
        writer.append_compaction(
            [Message(role="user", content="summary"), Message(role="assistant", content="tail")]
        )
    writer.close()

    result = load_session(tmp_path / f"{SESSION_ID}.jsonl")

    assert [message.content for message in result.messages] == ["old"]


def test_reopen_after_truncated_line_appends_a_new_parseable_line(tmp_path: Path) -> None:
    writer = SessionWriter(tmp_path, SESSION_ID, "model-a")
    writer.append_message(Message(role="user", content="first"))
    writer.close()
    with writer.path.open("ab") as file:
        file.write(b'{"type":"message"')

    reopened = SessionWriter.open_existing(tmp_path, SESSION_ID, "model-a")
    reopened.append_message(Message(role="assistant", content="after crash"))
    reopened.close()

    result = load_session(writer.path)
    assert [message.content for message in result.messages] == ["first", "after crash"]
    assert any("invalid JSON" in diagnostic for diagnostic in result.diagnostics)
