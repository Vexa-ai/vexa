- **Open sign-in is the operator's switch only (#1784).** `*` (anyone may sign in) is accepted in
  `VEXA_SIGNIN_ALLOW` alone. The sign-in list an admin edits in Settings now refuses it with a message
  saying so, and a `*` already saved there is ignored and named in a warning when admin-api starts.
