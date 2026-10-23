# sdk-capture.v1

Private native runtime capture wire, separate from joining. Start selects exactly one topology; stop unsubscribes without leaving. Permission pending, subscription and actual audio are distinct. PCM is base64 signed 16-bit little-endian mono at the reported 32/48 kHz. Timestamp is epoch milliseconds observed at native callback before thread/IPC queuing; it is not the SDK relative clock. Participant ID and optional name/self flag are carried on per-user frames. Do not log PCM or names in operational receipts.
