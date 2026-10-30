- **A personal transcription endpoint is tested before it is saved (#1924).** `PUT /user/transcription`
  sends one short audio request to the endpoint you'd end up with, through the outbound URL guard
  with no redirects. A rejected token, an account that can't pay, or a wrong path is refused with
  `422` (`transcription_endpoint_rejected`) and nothing is saved. An endpoint that can't be reached
  is still saved, and the response says so (`probe.kind: "unreachable"`). See [Settings](/api/settings).
