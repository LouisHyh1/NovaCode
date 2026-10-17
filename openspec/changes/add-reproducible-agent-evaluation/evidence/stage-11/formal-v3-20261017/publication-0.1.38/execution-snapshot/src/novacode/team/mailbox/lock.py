"""向后兼容的 mailbox lock 导出。"""

from novacode.team.filelock import acquire

__all__ = ["acquire"]
