- **Transcripts no longer stall on Kubernetes nodes with CPU limits (#1783).** The bot's speech
  segmentation started one thread per core of the host node, ignoring the Pod's CPU limit, so on a
  large node it used up the bot's whole CPU allowance and transcripts could stop appearing. It now
  sizes itself to the Pod's CPU allowance (at most 4 threads); set `BOT_ONNX_THREADS` to override.
