- **Meeting mail no longer says recordings and transcripts stay on the deployment's servers
  (S03-2).** The default sentence attendees read is now *"Vexa runs on this organisation's own
  servers; what you and your colleagues keep in your workspaces is visible to the company's
  agents."* The dropped clause was not true on every supported install: hosted transcription
  receives the audio, an external object store keeps the recordings, and a hosted model reads the
  transcript. The fallback line above the minutes mail no longer says it either. An operator who can
  promise where the words are kept writes it in `data_statement` (`_global/POLICIES.md` or
  `VEXA_FLOWS_DATA_STATEMENT`), which still replaces the first clause and reaches the mail as
  written. Instances seeded earlier keep the old sentence in their own `_global/STRUCTURE.md` until
  an administrator edits it.
