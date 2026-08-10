### Fixed

**A Teams meeting id that cannot be joined is refused at the API, not discovered by a dying bot.**
A Teams deep link needs `19:meeting_<id>@thread.v2` plus its context (tenant + organizer); the
numeric dial-in style id carries neither, and formatting it into the URL template produced a link
Teams answers with a redirect to its OAuth login. The bot then spent a full container lifecycle
probing a login form — every pre-join control "not found", admission polling 31s for an indicator
that could never appear — and exited 1, leaving the caller with a spawned bot and no transcript.
`POST /bots` now returns a typed 422 naming the id as the problem and asking for the invite link.
