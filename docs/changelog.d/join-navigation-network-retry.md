- **A bot whose first page load hits a network error tries again.** When the bot could not resolve
  the meeting host or reach it (`net::ERR_NAME_NOT_RESOLVED`, a refused or reset connection), it
  failed at once with a bare join failure. It now retries that page load up to three times over
  about 17 seconds, on Google Meet, Teams, Zoom and Jitsi. If every try fails, the meeting's reason
  says so: `navigation_network_error: net::ERR_… on 4 of 4 navigations`. Timeouts and pages that do
  load are not retried.
