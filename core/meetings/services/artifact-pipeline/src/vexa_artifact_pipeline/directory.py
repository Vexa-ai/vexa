"""Who a meeting's artifacts are for, and how to reach each of them.

Three sources, strongest first, deduplicated by the same fuzzy identity match the pre-send
gate uses (so the gate and the pipeline never disagree about who is a second person):

1. **The invitation** — an email and a name per attendee, from the mailroom binding. This
   is the authoritative roster: it says who was *asked* to be there, and it is addressable.
2. **The observed roster** — who the platform UI showed while the bot sat in the call.
   Names only; an address comes from the operator's address book or not at all.
3. **Speaker labels** — voices attributed in the transcript that match nobody above.

Two rules keep the third source safe. A speaker label yields an artifact, never an address:
with no email the delivery stage returns ``no_address`` and nothing leaves the building. And
the pre-send gate still governs whether *any* of these people may receive anything — the
directory decides who exists, the gate decides who may be written to. Including speakers is
what lets a record with no roster at all (three in the calibration corpus) still produce the
per-person artifacts the identity join will consume, without inventing a recipient.

The creator is always present: they convened the meeting, they are the fall-back recipient
when the gate holds, and half the archive's rosters omit the account owner.
"""

from __future__ import annotations

from typing import Mapping, Sequence

from presend_gate.signals import is_bot_label, same_person

from .artifact import Recipient
from .ports import CompletedMeeting, FetchedRecord
from .transcript import observed_roster, speaker_counts

#: A speaker label under this share of attributed turns is treated as attribution noise
#: rather than a participant. Speaker attribution is the weakest input the pipeline has.
MIN_SPEAKER_TURN_SHARE = 0.02


class RosterDirectory:
    """The v0 directory: invitation → observed roster → speaker labels, plus an address book.

    ``address_book`` maps a display name to an email. It is how a dev run reaches real
    inboxes before the mailroom's invite roster is wired to the trigger; it is operator
    data, never inferred.
    """

    def __init__(
        self,
        *,
        address_book: Mapping[str, str] | None = None,
        include_speakers: bool = True,
        min_speaker_turn_share: float = MIN_SPEAKER_TURN_SHARE,
    ) -> None:
        self._book = {k.strip().lower(): v for k, v in (address_book or {}).items()}
        self._include_speakers = include_speakers
        self._min_share = min_speaker_turn_share

    def resolve(
        self, record: FetchedRecord, trigger: CompletedMeeting
    ) -> Sequence[Recipient]:
        people: list[Recipient] = []

        def add(display_name: str, email: str | None = None, *, is_creator: bool = False) -> None:
            name = (display_name or "").strip()
            if not name:
                return
            for i, existing in enumerate(people):
                if same_person(existing.display_name, name):
                    # Keep the richer entry: an address and the creator flag both win.
                    people[i] = Recipient(
                        display_name=existing.display_name,
                        email=existing.email or email or self._lookup(name),
                        is_creator=existing.is_creator or is_creator,
                    )
                    return
            people.append(
                Recipient(
                    display_name=name,
                    email=email or self._lookup(name),
                    is_creator=is_creator,
                )
            )

        if trigger.creator:
            add(trigger.creator, trigger.creator_email, is_creator=True)

        for entry in trigger.invite_participants:
            add(str(entry.get("name") or entry.get("email") or ""), entry.get("email"))

        for name in observed_roster(record):
            add(name)

        if self._include_speakers:
            counts = speaker_counts(record)
            total = sum(counts.values()) or 1
            for label, count in counts.most_common():
                if count / total < self._min_share:
                    continue
                if is_bot_label(label, trigger.bot_names):
                    continue
                add(label)

        return tuple(people)

    def _lookup(self, display_name: str) -> str | None:
        direct = self._book.get(display_name.strip().lower())
        if direct:
            return direct
        for name, email in self._book.items():
            if same_person(name, display_name):
                return email
        return None
