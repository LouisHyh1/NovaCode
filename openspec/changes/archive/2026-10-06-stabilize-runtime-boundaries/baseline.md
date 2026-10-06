# Implementation baseline

Recorded on 2026-07-31 before implementation changes.

| Gate | Command | Result |
| --- | --- | --- |
| Locked dependencies | `uv lock --check` | PASS (`Resolved 55 packages`) |
| Full regression | `uv run --locked pytest -q` | PASS (`605 passed in 12.10s`) |
| Ruff lint | `uv run --locked ruff check src tests` | PASS |
| Version smoke | `uv run --locked python -m novacode --version` | PASS (`0.1.14`) |
| Existing Team/Search behavior | `uv run --locked pytest -q tests/team tests/task tests/test_tool.py tests/test_sensitive_permission.py` | PASS (`53 passed in 2.41s`) |

The baseline records behavior only. It does not authorize using `docs/` as an
implementation or acceptance source.
