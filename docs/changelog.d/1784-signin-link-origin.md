- **Emailed sign-in links use only the configured public URL (#1784).** The link points at
  `NEXTAUTH_URL` (or `TERMINAL_URL`) and never at the host a request names. With neither set, the
  terminal sends no link and the form reports that email sign-in is not configured. Set `NEXTAUTH_URL`
  to the terminal's public origin (Helm: `terminal.publicUrl`; Lite: `TERMINAL_PUBLIC_URL`, which
  defaults to `http://localhost:3001`). See [Publishing behind a reverse proxy](/deployment#publishing-behind-a-reverse-proxy).
