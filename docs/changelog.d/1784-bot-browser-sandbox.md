- **Meeting bots' browsers run in Chromium's sandbox, with an environment of their own (#1784).** The
  bot image runs as a non-root uid (10002) and each bot's own container runs under a seccomp profile
  that allows the user namespaces the sandbox is built on (Docker's default profile plus that one
  rule; Apache-2.0, see `THIRD_PARTY_LICENSES.md`). Compose and Lite need nothing new. On Kubernetes
  the chart installs the profile on the nodes with a `<release>-bot-seccomp` DaemonSet
  (`runtime.botSandbox`), which needs a namespace that admits a `hostPath` volume; on OpenShift
  install it by MachineConfig or the Security Profiles Operator and allow it in the bots' SCC (chart
  README, *The meeting bots' browser sandbox*), or set `runtime.botSandbox.enabled=false`. Bot Pods
  now ask for `runAsNonRoot`, so **keep the bot image on this release's** (an older, root image will
  not start). Each bot logs `Chromium runs with its sandbox`, or why it does not.
- **Lite: no shared X display or VNC view (#1784).** Each bot brings up its own display as its own
  user; `VEXA_LITE_VNC` is gone.
