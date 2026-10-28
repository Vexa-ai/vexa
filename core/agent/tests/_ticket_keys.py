"""A throwaway agent-api ticket key on disk, and the door's check for its public half."""
from __future__ import annotations

from control_plane import identity_token, subject_ticket, ticket_mint


def install(tmp_path, monkeypatch):
    key = identity_token.generate_signing_key()
    path = tmp_path / "agent-subject-ticket.pem"
    path.write_bytes(identity_token.private_key_pem(key))
    monkeypatch.setenv(ticket_mint.KEY_ENV, str(path))
    monkeypatch.setattr(ticket_mint, "_signers", {})
    return key


def checks(key):
    pub = key.public_key()

    def check(message, signature):
        try:
            pub.verify(signature, message)
            return True
        except Exception:  # noqa: BLE001
            return False

    return {subject_ticket.AGENT: check}
