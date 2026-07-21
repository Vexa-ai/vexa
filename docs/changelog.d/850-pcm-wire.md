### Fixed

**PCM crosses the bot's page→node hop as base64, not as a JSON number array.** A 4096-sample frame
is 16.0 KB raw; encoding it as decimal numbers cost 80.8 KB — 5.05× — because every sample became a
~20-character string. At four frames a second that is 1.2 GB per hour per bot of pure serialization
overhead, which the silence-gate fix would otherwise have made continuous. Base64 of the same bytes
is 21.3 KB, so the hop drops to 0.3 GB/hour, bit-exact.
