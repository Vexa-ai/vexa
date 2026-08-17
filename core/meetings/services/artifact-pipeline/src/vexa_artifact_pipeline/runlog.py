"""Record — the append-only stream, which is also the idempotency oracle.

One line per run, holding what the run decided and what happened to each person: the
requested id and the record's own id, the gate verdict with its reasons and signals, the
participants the directory resolved, the recipients the gate authorized, and a per-recipient
delivery outcome.

**It is one store, not two.** "What has already gone out" is *read back from the same lines
that recorded it* rather than kept in a parallel ledger, because a second store can disagree
with the first and the disagreement would be silent — the failure mode where a duplicate
email is sent while a table says it wasn't.

It is also the fixture stream. The artifacts, the verdicts and the signals are all in the
line, so a run over a real archive is replayable: the delta between two renderers over the
same corpus, or between two gate policies, is a diff of these files. That is what the
offline judge will be trained against, so the shape is deliberately boring — newline-
delimited JSON, no nesting beyond one level of list, every id a string.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterator, Mapping

RUN_LOG_SCHEMA_VERSION = 1


class JsonlRunLog:
    """Append-only newline-delimited JSON on local disk.

    Writes are ``O_APPEND`` single-``write`` calls, so two processes appending to the same
    file interleave whole lines rather than corrupting each other's.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, entry: Mapping[str, Any]) -> None:
        line = json.dumps(entry, ensure_ascii=False, sort_keys=False) + "\n"
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())

    def entries(self) -> Iterator[dict[str, Any]]:
        if not self.path.exists():
            return
        with open(self.path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    parsed = json.loads(line)
                except ValueError:
                    continue
                if isinstance(parsed, dict):
                    yield parsed

    def delivered_identities(self, meeting_id: str) -> frozenset[str]:
        """Identities this meeting's artifact has already reached, successfully.

        Only ``sent`` counts. A ``no_address`` or a ``failed`` recipient is *not* delivered
        and must be retried on the next run — the whole point of keying on the outcome
        rather than on the attempt.
        """
        out: set[str] = set()
        for entry in self.entries():
            if str(entry.get("meeting_id")) != str(meeting_id):
                continue
            for outcome in entry.get("outcomes") or ():
                if isinstance(outcome, Mapping) and outcome.get("status") == "sent":
                    identity = outcome.get("identity")
                    if identity:
                        out.add(str(identity))
        return frozenset(out)


class MemoryRunLog:
    """The same contract, in memory. For a dry run and for tests that assert the stream."""

    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    def append(self, entry: Mapping[str, Any]) -> None:
        self.records.append(json.loads(json.dumps(entry, ensure_ascii=False)))

    def entries(self) -> Iterator[dict[str, Any]]:
        return iter(list(self.records))

    def delivered_identities(self, meeting_id: str) -> frozenset[str]:
        out: set[str] = set()
        for entry in self.records:
            if str(entry.get("meeting_id")) != str(meeting_id):
                continue
            for outcome in entry.get("outcomes") or ():
                if outcome.get("status") == "sent" and outcome.get("identity"):
                    out.add(str(outcome["identity"]))
        return frozenset(out)


__all__ = ["JsonlRunLog", "MemoryRunLog", "RUN_LOG_SCHEMA_VERSION"]
