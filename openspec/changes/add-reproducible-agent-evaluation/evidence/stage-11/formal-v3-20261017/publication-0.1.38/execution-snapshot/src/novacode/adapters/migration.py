"""JSON schema 迁移的不可覆盖备份辅助。"""

from __future__ import annotations

import os
from pathlib import Path


def migration_backup_path(path: Path) -> Path:
    return path.with_name(f"{path.name}.migration-v0.bak")


def write_migration_backup(path: Path) -> Path:
    """逐字节创建备份；已有备份永不覆盖。"""
    backup = migration_backup_path(path)
    try:
        stream = backup.open("xb")
    except FileExistsError:
        return backup
    try:
        stream.write(path.read_bytes())
        stream.flush()
        os.fsync(stream.fileno())
    finally:
        stream.close()
    return backup
