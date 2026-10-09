- **Meeting rooms are marked as rooms, not mistaken for people (#1689).** When a Google Meet
  hardware kit, a Microsoft Teams Room or a Zoom Room is in the call, every person in that room
  speaks through the kit's single mixed stream and is attributed to the kit's display name —
  Google's own Meet transcripts do the same. Transcript segments now carry
  `speaker_kind: "room" | "person" | "unknown"`, and each row of `GET …/participants` carries the
  same value as `kind`, so a downstream consumer can tell a device name from a person's name.
  Attribution is unchanged and in-room people are still **not** separated. `person` means "no room
  marker found", never "confirmed human" — a kit named after a human reads `person`, and the fix
  for that is renaming the device. Self-hosted deployments extend the pattern table with
  `VEXA_ROOM_PATTERNS`.
