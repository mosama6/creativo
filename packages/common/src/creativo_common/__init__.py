"""Shared platform primitives."""

from creativo_common.ids import new_id
from creativo_common.settings import PlatformSettings, get_settings
from creativo_common.timeutil import utcnow

__all__ = ["PlatformSettings", "get_settings", "new_id", "utcnow"]
