"""Write the subject-ticket.v1 goldens (golden/*.json) from the canonical module.

Deterministic: every vector is minted by `subject_ticket.mint` at a fixed `now`, with its random
`jti` pinned for the run, and signed with a key RFC 8032 publishes (section 7.1 TEST 1; TEST 2 for
"another key"). Each refusal is checked against `subject_ticket.read` before it is written, so a
golden's `reason` is what the Python verifier answers. Ed25519 is deterministic, so re-running this
reproduces every file byte for byte.

    uv run --with cryptography python core/gateway/contracts/subject-ticket.v1/make_goldens.py
"""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import subject_ticket as st  # noqa: E402

TEST1 = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(
    "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60"))
TEST2 = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(
    "4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb"))
PUBLIC = TEST1.public_key().public_bytes(
    serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
NOW = 1767225600  # 2026-01-01T00:00:00Z


def _check(key):
    pub = key.public_key()

    def check(message: bytes, sig: bytes) -> bool:
        try:
            pub.verify(sig, message)
            return True
        except Exception:  # noqa: BLE001
            return False
    return check


CHECKS = {iss: _check(TEST1) for iss in st.ISSUERS}


def _mint(key, jti: str, **kw) -> str:
    real = st.secrets
    st.secrets = type("Pinned", (), {"token_hex": staticmethod(lambda n: jti)})
    try:
        return st.mint(key.sign, now=NOW, **kw)
    finally:
        st.secrets = real


def _raw(key, claims: dict) -> str:
    """A token over `claims` as given — what a producer outside the policy would sign."""
    body = st.VERSION + "." + st._b64u(json.dumps(claims, sort_keys=True, separators=(",", ":")).encode())
    return body + "." + st._b64u(key.sign(body.encode("ascii")))


def _claims(token: str) -> dict:
    return json.loads(base64.urlsafe_b64decode(token.split(".")[1] + "=="))


def _write(name: str, data: dict) -> None:
    (HERE / "golden" / name).write_text(json.dumps(data, indent=2) + "\n")


VECTORS = {
    "vector-meetings-event.json": ("meeting.completed for the meeting's owner (post-meeting processing)",
        dict(iss=st.MEETINGS, kind=st.EVENT, sub="42", evt="meeting.completed:7781",
             cls=[st.TRANSCRIPT_READ, st.CHAT_AUTONOMOUS, st.DESK_WRITE, st.ROOM_BILLS_TO],
             ttl=3 * 86400, mid=7781, native="google_meet/abc-defg-hij"), "0f1e2d3c4b5a69788796a5b4"),
    "vector-agent-grant.json": ("a routine the person turned on from their own session",
        dict(iss=st.AGENT, kind=st.GRANT, sub="42", evt="user:42/morning-brief",
             cls=[st.ROUTINE_TURN, st.BOT_DISPATCH], ttl=400 * 86400), "a1b2c3d4e5f60718293a4b5c"),
    "vector-mail-invite.json": ("an authenticated invite: its organizer, meeting and attendees; the bot once",
        dict(iss=st.MAIL, kind=st.EVENT, sub="42", addr="Organizer@Example.com", evt="invite.received:m-311",
             cls=[st.BOT_DISPATCH, st.TRANSCRIPT_READ, st.DESK_WRITE, st.CHAT_AUTONOMOUS],
             once=[st.BOT_DISPATCH], ttl=10 * 86400, native="zoom/83012345678",
             peers=["bob@example.com", "Carol@Example.org"]), "5c4b3a2918f7e6d5c4b3a291"),
    "vector-gateway-event.json": ("the person's own bearer, validated through the door",
        dict(iss=st.GATEWAY, kind=st.EVENT, sub="42", evt="validate",
             cls=[st.DESK_WRITE, st.ROUTINE_TURN, st.BOT_DISPATCH], ttl=600), "00112233445566778899aabb"),
}


def main() -> int:
    (HERE / "golden").mkdir(exist_ok=True)
    for name, (what, kw, jti) in VECTORS.items():
        ttl = kw["ttl"]
        token = _mint(TEST1, jti, **kw)
        claims = st.read(token, CHECKS, now=NOW)
        _write(name, {"what": what, "public_key": PUBLIC, "now": NOW, "ttl_sec": ttl,
                      "claims": claims, "token": token})

    base = json.loads((HERE / "golden" / "vector-meetings-event.json").read_text())
    good, good_claims = base["token"], base["claims"]
    head, payload, sig = good.split(".")
    tampered = {**good_claims, "sub": "43"}
    tampered_payload = st._b64u(json.dumps(tampered, sort_keys=True, separators=(",", ":")).encode())
    refusals = {
        "refused-other-key.json": ("the meetings vector's claims signed by another key (RFC 8032 TEST 2)",
                                   _raw(TEST2, good_claims), NOW, "bad_signature"),
        "refused-tampered.json": ("the meetings vector with its subject changed after signing",
                                  f"{head}.{tampered_payload}.{sig}", NOW, "bad_signature"),
        "refused-unsigned.json": ("the meetings vector with its signature removed",
                                  f"{head}.{payload}.", NOW, "bad_signature"),
        "refused-other-version.json": ("the meetings vector under another scheme name",
                                       f"st2.{payload}.{sig}", NOW, "malformed"),
        "refused-expired.json": ("the meetings vector read at its expiry",
                                 good, good_claims["exp"], "expired"),
        "refused-not-yet-valid.json": ("the meetings vector read more than 60 s before it was issued",
                                       good, NOW - 61, "not_yet_valid"),
        "refused-lifetime.json": ("a correctly signed gateway event ticket claiming 601 s",
                                  _raw(TEST1, {**json.loads((HERE / "golden" / "vector-gateway-event.json").read_text())["claims"],
                                               "exp": NOW + 601}), NOW, "lifetime_too_long"),
        "refused-issuer-not-allowed.json": ("a correctly signed agent ticket opening transcript.read, which agent may not grant",
                                            _raw(TEST1, {"v": st.TYPE, "iss": st.AGENT, "kind": st.EVENT, "sub": "42",
                                                         "cls": [st.TRANSCRIPT_READ], "evt": "thread.message:9",
                                                         "iat": NOW, "exp": NOW + 3600, "jti": "ffeeddccbbaa998877665544"}),
                                            NOW, "issuer_not_allowed"),
        "refused-unknown-issuer.json": ("a correctly signed ticket naming an issuer the verifier holds no key for",
                                        _raw(TEST1, {**good_claims, "iss": "crm"}), NOW, "unknown_issuer"),
    }
    for name, (what, token, now, reason) in refusals.items():
        try:
            st.read(token, CHECKS, now=now)
            raise SystemExit(f"{name}: read() accepted it")
        except st.TicketError as e:
            if e.reason != reason:
                raise SystemExit(f"{name}: read() says {e.reason}, the golden says {reason}")
        _write(name, {"what": what, "public_key": PUBLIC, "now": now, "token": token, "reason": reason})
    print(f"subject-ticket.v1: wrote {len(VECTORS)} vectors and {len(refusals)} refusals")
    return 0


if __name__ == "__main__":
    sys.exit(main())
