- **Text-only custom STT responses now reach the transcript (#1148).** OpenAI-compatible
  endpoints may return only `text`; the mixed pipeline now assigns that text to the submitted
  speech window instead of discarding it for missing provider timestamps. See
  [Custom STT endpoints](/how-to/custom-stt).
