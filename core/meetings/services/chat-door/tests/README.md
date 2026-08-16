# chat-door/tests — what is proven, and how

```bash
uv run pytest -q     # from core/meetings/services/chat-door
```

No docker, no network, no live stack — the shipped app is driven in-process (`TestClient`),
the meeting API is an `httpx.MockTransport`, and SMTP is a real socket served by a stub in
this directory.

| File | Proves |
|---|---|
| [`test_tokens.py`](test_tokens.py) | round-trip · expiry · **single-use** (a replayed link is refused) · sessions stay reusable · tamper and wrong-key both fail as signature errors · kind confusion refused · an expired token reports expiry rather than silently burning its id · the signing key never renders in `repr`/`str`/config |
| [`test_door_flow.py`](test_door_flow.py) | **nothing is stored before the first click** · the first successful verify creates the user + an empty personal doc · a *failed* verify creates nothing · a second click does not re-create · the link dies after one use while the session cookie carries on · scope refusal across meetings (page and steer) · unknown scope degrades to `guest` · transcript fallback to the route that exists today · empty transcript stated, not implied · steer appends dated entries and keeps non-ASCII intact |
| [`test_postman.py`](test_postman.py) | the record line beats the directory name (the corpus disagrees with itself and the door needs the record's own id) · subject in the artifact's own language · placeholder link rewritten exactly once · `multipart/alternative` with the link in both parts · headers naming record and participant · the minted link is scoped to that record and recipient · a Russian artifact survives serialization · **a real SMTP send** · the CLI's `--dry-run`, and that it does not echo the link |
| [`test_local_records.py`](test_local_records.py) | the dev record source resolves by the record's own id, states a miss, and **labels every page it serves** as file-sourced |
| [`test_end_to_end.py`](test_end_to_end.py) | the coupling: artifact file → SMTP → the delivered message → the link inside it → the door → the personal-instructions write. Plus the mismatched-key case, which must fail closed and create nothing. |
| [`smtp_stub.py`](smtp_stub.py) | ~60 lines of SMTP (EHLO/MAIL/RCPT/DATA/QUIT) capturing raw message bytes. Python removed `smtpd` in 3.12 and container workloads do not run on this host, so the test lane owns its own sink. |
| [`conftest.py`](conftest.py) | the fake meeting API (including a switch to make the record-keyed route answer 405) and a door wired to a temp store. |
