# Teams pipeline debug resource

This dynamic route serves only the local Teams pipeline witness page. When the parent bridge is
explicitly enabled in development, `events`, `start`, and `status` proxy the loopback replay
backend, `audio` proxies its byte-range WAV response, and `reference` reads the one configured
reference JSON file. Unknown resources and every non-development request return `404`.
