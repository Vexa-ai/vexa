"""The transit answer's signature (ADR-0051): what a target checks on identity's reply."""
import time

from admin_api.app.transit import sign_answer, verify_answer
from admin_api.key_store import token_hint

SECRET = "vxts_target_credential"


def _claims(**over):
    now = int(time.time())
    return {"aud": "transcription", "sub": 7, "email": "a@b.c", "actions": ["audio.transcribe"],
            "key_id": 1, "iat": now, "exp": now + 60, **over}


def test_a_signed_answer_verifies_for_its_target_and_action():
    assert verify_answer(SECRET, sign_answer(SECRET, _claims()), audience="transcription",
                         action="audio.transcribe")["sub"] == 7


def test_wrong_secret_audience_action_expiry_or_tamper_refuse():
    signed = sign_answer(SECRET, _claims())
    assert verify_answer("other", signed, audience="transcription", action="audio.transcribe") is None
    assert verify_answer(SECRET, signed, audience="summaries", action="audio.transcribe") is None
    assert verify_answer(SECRET, signed, audience="transcription", action="audio.translate") is None
    expired = sign_answer(SECRET, _claims(exp=int(time.time()) - 1))
    assert verify_answer(SECRET, expired, audience="transcription", action="audio.transcribe") is None
    forged = {**signed, "answer": sign_answer(SECRET, _claims(sub=8))["answer"]}
    assert verify_answer(SECRET, forged, audience="transcription", action="audio.transcribe") is None


def test_a_transit_key_hint_names_its_target():
    assert token_hint("vxt_transcription_Ab3dEfGh") == "vxt_transcription_…EfGh"
