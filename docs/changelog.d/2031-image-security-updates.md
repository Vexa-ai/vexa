- **The bot and Lite images take Ubuntu's security updates on every build again (#2031).** Their
  `apt-get upgrade` step was being reused from the build cache: a bot image built on 2 Nov still
  carried the packages of 10 Oct, without Ubuntu's 26 Oct brotli fix (CVE-2026-111497). The step now
  reruns whenever Ubuntu publishes a security update. The bot's AWS CLI is pinned to 2.39.3 and
  checked against its SHA-256.
