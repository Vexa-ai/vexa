"""Strict parser for the single structured development-notification environment value."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import jsonschema

from .service import PostMeetingFault


@dataclass(frozen=True)
class DevNotificationConfig:
    recipient: str
    terminal_url: str
    smtp: str
    sender: str


def parse_dev_notification_config(raw: str) -> DevNotificationConfig:
    try:
        value = json.loads(raw)
        schema = json.loads((Path(__file__).with_name("config.v1.json")).read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker()).validate(value)
        return DevNotificationConfig(**value)
    except (ValueError, OSError, jsonschema.ValidationError, TypeError) as exc:
        raise PostMeetingFault(
            source="config", kind="invalid", detail=f"invalid VEXA_POST_MEETING_DEV_EMAIL: {exc}",
        ) from exc
