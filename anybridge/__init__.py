"""AnyBridge — expose websites and Git repositories to coding agents."""

from .browser import PageBridge
from .session import BridgeSession

__version__ = "1.0.0"
__all__ = ["BridgeSession", "PageBridge"]
