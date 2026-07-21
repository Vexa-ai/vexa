- **Transcription quality framework and fixture corpus (#847, #848).** The eval tree gains a single
  metric set with two loops (`core/meetings/eval/FRAMEWORK.md`) and a fixture corpus in which a
  witnessed session becomes a permanent, offline regression test with a promoter and a scorer
  (`core/meetings/eval/CORPUS.md`). Contributors can now judge a transcription change on merit
  without booking a live meeting. See [the eval framework](https://github.com/Vexa-ai/vexa/tree/main/core/meetings/eval).
- **Two transcription-quality fixes pinned by the corpus (#838, #839).** A Google Meet end-of-stream
  turn now transcribes the audio it owes instead of publishing a stale draft, and a mixed-lane draft
  confirms under its own `segment_id` so a sentence is stored once rather than duplicated.
