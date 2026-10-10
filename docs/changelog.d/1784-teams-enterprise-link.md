- **Teams enterprise invitation links work as they are (#1784).** Minutes accepts Teams enterprise
  `/meet/` links and web-client fragment URLs, including 16-digit meeting ids. The agent checks the
  provider's host name and keeps the full invitation URL and passcode when it sends the bot; look-alike
  hosts and embedded URLs are refused.
