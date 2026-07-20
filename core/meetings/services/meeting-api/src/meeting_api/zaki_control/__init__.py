"""ZAKI Minutes' sealed control-plane adapter.

The package owns only the Hub BFF -> meeting-api boundary.  Browser clients never
receive its credentials or engine address; the app composition root decides whether
the router is mounted at all.
"""

from .router import ControlConfig, build_router

__all__ = ["ControlConfig", "build_router"]
