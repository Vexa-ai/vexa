### Added

**The mixed lane now has a quality number that costs no meeting.** A live transcript is scored
against KNOWN truth — the exact words and speakers of a synthetic session built offline — so recall,
precision, and attribution are measured without a live call, a labeller, or a reference the pipeline
could bias. The meeting-free mixed-lane oracle reads recall 0.924 / precision 0.936 / attribution
0.947, identical across three runs. The reference self-calibrates (G3): a second single-pass pass
with shifted cuts prices what one pass misses at a seam, retiring the jitsi entry's "27.7% invention"
as a reference artifact, not a pipeline defect.
