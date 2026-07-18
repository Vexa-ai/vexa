# 1-audio — the committed stage-1 counting WAVs

One WAV per turn (`turnNNN.wav`), linear16 16 kHz mono, Deepgram Aura voices: the speakers count
1..20 switching every 5 numbers (see `../truth.jsonl` for who says what, `../README.md` for
provenance + regeneration). Input to the L3.5 wav→words leg's local CPU whisper.
