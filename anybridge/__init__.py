"""anybridge — expose any web page to any agent."""

from .browser import PageBridge
from .session import BridgeSession

__version__ = "0.4.0"
__all__ = ["BridgeSession", "PageBridge"]
