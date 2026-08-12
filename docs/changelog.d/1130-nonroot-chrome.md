- **The meeting bot runs Chromium sandboxed, as a non-root user (#1130).** The recorder's browser no
  longer launches as root with `--no-sandbox` — it runs as an unprivileged user with Chromium's real
  namespace sandbox, so the renderer is properly sandboxed and the *"You are using an unsupported
  command-line flag"* security banner is gone from recordings. This needs the bot container to permit
  user namespaces: the Kubernetes and Docker runtime backends set `seccomp: Unconfined` on bot
  containers automatically, but the all-in-one **Lite** container must be launched with
  `security_opt: [seccomp=unconfined]` in your compose/stack. Set `CHROME_NO_SANDBOX=1` to keep the
  legacy root + `--no-sandbox` path on hosts that can't relax seccomp. See [Configuration](/configuration)
  and [Deploy Lite](/deployment-lite).
