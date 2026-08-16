# vexa_mailroom (package) — ports · parser · loop · adapters

The mailroom's logic, injectable. Public surface is `__init__.py`: **`Mailroom`** (the loop),
**`parse_invite`** (the pure parser) and **`create_app`** (liveness + the operator routes).

| module | what |
|---|---|
| **`ports.py`** | the four ports — `MailSource` · `MeetingApi` · `BindingStore` · `NoticeSink` — plus the `MailMessage` / `Binding` / `Notice` structs. The service talks to nothing else, which is what lets the tests drive shipped code with fakes and lets Mailpit be replaced by IMAP later. |
| **`invite.py`** | `parse_invite(raw) -> ParsedMail`: RFC-822 bytes → an invitation. MIME walk (`text/calendar`, `.ics` attachments of any content-type), `METHOD`, `UID`, `SEQUENCE`, `RRULE`/`EXDATE`, roster, and the joinable link. Never raises — a failure is a `Rejection`. |
| **`meeting_link.py`** | the VENDORED meeting-URL parser (the copy `meeting_api.collector.meeting_link` owns). Keep the two in step: the mailroom hands `meeting_url` to `POST /meetings`, which parses it again. |
| **`service.py`** | `Mailroom.poll_once()` — ingest → parse → resolve → act → advance the cursor, plus `advance_series()`, the sweep that rolls a recurring binding to its next occurrence. The product decisions are documented here, in order. |
| **`store.py`** | `MemoryStore` / `FileStore`: bindings, the resume cursor (stamp + seen ids) and the notice log, in one atomically-written JSON file. |
| **`adapters.py`** | production ports: `MailpitSource` (dev mailbox) and `MeetingApiClient` (the public API with `X-API-Key`). Both take an injectable `httpx` transport; neither raises on a refusal. |
| **`config.py`** | environment → `Settings`. `MAILROOM_WORKSPACE_MAP` is the workspace resolution. |
| **`app.py`** | `create_app(mailroom)` → `/health` + the guarded `/internal` routes. |
| **`__main__.py`** | the composition root: real adapters, the poll loop, and the degrade path when the mailbox is unconfigured. |
