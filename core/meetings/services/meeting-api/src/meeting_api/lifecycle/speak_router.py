"""Active-meeting voice commands: HTTP speak requests → sealed acts.v1 Redis messages."""
from __future__ import annotations

import base64
import binascii
import io
import json
import wave
from pathlib import Path
from typing import Optional

import jsonschema
from fastapi import APIRouter, Header, HTTPException, Request
from referencing import Registry, Resource

from ..bot_spawn.ports import MeetingRepo
from .stop_router import CommandPublisher, _SUPPORTED_PLATFORMS, _resolve_user_id

_MAX_AUDIO_BYTES = 10 * 1024 * 1024


def _load_acts_schema() -> dict:
    relative = Path("meetings") / "contracts" / "acts.v1" / "acts.schema.json"
    for parent in Path(__file__).resolve().parents:
        candidate = parent / relative
        if candidate.is_file():
            return json.loads(candidate.read_text())
    raise FileNotFoundError(f"sealed contract not found by path: {relative}")


_ACTS_SCHEMA = _load_acts_schema()
_ACTS_REGISTRY = Registry().with_resource(
    _ACTS_SCHEMA["$id"], Resource.from_contents(_ACTS_SCHEMA)
)


def _validate_act(act: dict) -> None:
    jsonschema.Draft202012Validator(
        {"$ref": f"{_ACTS_SCHEMA['$id']}#/$defs/Act"}, registry=_ACTS_REGISTRY
    ).validate(act)


def _decode_audio(value: object) -> bytes:
    if not isinstance(value, str) or not value:
        raise HTTPException(status_code=422, detail="audio_base64 must be a non-empty base64 string")
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as error:
        raise HTTPException(status_code=422, detail="audio_base64 is not valid base64") from error
    if len(decoded) > _MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="decoded audio exceeds the 10 MiB limit")
    return decoded


def _wav_from_pcm(pcm: bytes, sample_rate: object) -> bytes:
    if not isinstance(sample_rate, int) or isinstance(sample_rate, bool) or not 8000 <= sample_rate <= 96000:
        raise HTTPException(status_code=422, detail="sample_rate must be an integer from 8000 to 96000")
    if len(pcm) % 2:
        raise HTTPException(status_code=422, detail="PCM audio must contain complete s16le samples")
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    return output.getvalue()


def _validate_wav(payload: bytes) -> None:
    try:
        with wave.open(io.BytesIO(payload), "rb") as wav:
            if wav.getnchannels() < 1 or wav.getsampwidth() < 1 or wav.getframerate() < 1:
                raise wave.Error("invalid format")
    except (wave.Error, EOFError) as error:
        raise HTTPException(status_code=422, detail="audio_base64 is not a valid WAV file") from error


async def _active_meeting(repo: MeetingRepo, user_id: int, platform: str, native: str) -> dict:
    if platform not in _SUPPORTED_PLATFORMS:
        raise HTTPException(status_code=422, detail=f"unsupported platform '{platform}'")
    meeting = await repo.find_active(user_id, platform, native)
    if not meeting:
        raise HTTPException(status_code=404, detail="No active meeting for this bot")
    return meeting


async def _publish(publisher: CommandPublisher, meeting_id: object, act: dict) -> None:
    _validate_act(act)
    try:
        await publisher.publish(f"bot_commands:meeting:{meeting_id}", json.dumps(act))
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(status_code=503, detail="voice command bus unavailable; retry") from error


def build_speak_router(repo: MeetingRepo, publisher: CommandPublisher) -> APIRouter:
    router = APIRouter()

    @router.post("/bots/{platform}/{native_meeting_id}/speak")
    async def speak(
        platform: str,
        native_meeting_id: str,
        request: Request,
        x_user_id: Optional[str] = Header(default=None),
    ):
        user_id = _resolve_user_id(x_user_id)
        meeting = await _active_meeting(repo, user_id, platform, native_meeting_id)
        try:
            body = await request.json()
        except Exception as error:
            raise HTTPException(status_code=422, detail="invalid JSON body") from error
        if not isinstance(body, dict):
            raise HTTPException(status_code=422, detail="body must be an object")

        text = body.get("text")
        audio = body.get("audio_base64")
        audio_url = body.get("audio_url")
        provided = sum(value is not None and value != "" for value in (text, audio, audio_url))
        if provided != 1:
            raise HTTPException(status_code=422, detail="provide exactly one of text, audio_base64, or audio_url")
        if audio_url is not None:
            raise HTTPException(status_code=422, detail="audio_url is not supported; send WAV or PCM as audio_base64")

        if text is not None:
            if not isinstance(text, str) or not text.strip():
                raise HTTPException(status_code=422, detail="text must be a non-empty string")
            act = {"action": "speak", "text": text.strip()}
            voice = body.get("voice")
            if isinstance(voice, str) and voice and voice != "auto":
                act["voice"] = voice
        else:
            raw = _decode_audio(audio)
            audio_format = body.get("format", "wav")
            if audio_format == "pcm":
                raw = _wav_from_pcm(raw, body.get("sample_rate", 24000))
            elif audio_format == "wav":
                _validate_wav(raw)
            else:
                raise HTTPException(status_code=422, detail="audio format must be wav or pcm")
            act = {"action": "speak_audio", "audioBase64": base64.b64encode(raw).decode("ascii")}

        await _publish(publisher, meeting["id"], act)
        return {"status": "accepted", "meeting_id": meeting["id"]}

    @router.delete("/bots/{platform}/{native_meeting_id}/speak")
    async def stop_speaking(
        platform: str,
        native_meeting_id: str,
        x_user_id: Optional[str] = Header(default=None),
    ):
        user_id = _resolve_user_id(x_user_id)
        meeting = await _active_meeting(repo, user_id, platform, native_meeting_id)
        await _publish(publisher, meeting["id"], {"action": "speak_stop"})
        return {"status": "accepted", "meeting_id": meeting["id"]}

    return router
