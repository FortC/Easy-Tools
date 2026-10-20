"""Privacy-related policy helpers shared by the app and tests."""

from __future__ import annotations

from typing import Any, Dict


def notifications_allowed(config: Dict[str, Any]) -> bool:
    """Windows toast notifications are external windows and cannot inherit affinity."""
    trigger = config.get("trigger") or {}
    privacy = config.get("privacy") or {}
    return (bool(trigger.get("notify_enabled", True))
            and not bool(privacy.get("exclude_from_capture", False)))
