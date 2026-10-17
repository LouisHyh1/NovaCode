"""声明式生命周期 Hook。"""

from novacode.hook.engine import DispatchResult, Engine
from novacode.hook.event import Event, is_blocking
from novacode.hook.loader import load

__all__ = ["DispatchResult", "Engine", "Event", "is_blocking", "load"]
