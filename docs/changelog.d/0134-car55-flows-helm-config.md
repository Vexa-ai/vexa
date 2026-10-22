- **Every flows setting can be set on Helm, and the config gate now checks the flows chart.** The
  chart has new values for the flows dials that it did not expose before: `flows.timelineKey`,
  `flows.mail.{inbox,replyTo,relay,captureSmtp,maxPerDay,kindsDefault,inboundFilter,bodyMax,notifyChannel,graph.*,smtp.from}`,
  `flows.outbound.*`, `flows.jitsiHosts`, `flows.nudges.*` and `flows.limits.*`. Each one renders
  only when set, so existing installs render the same as before, and `flows.extraEnv` still wins.
  The chart runs no capture double: set `flows.mail.captureSmtp` whenever you use
  `flows.mail.recipientAllow` or `relay: capture`. See [Flows operations](/flows/operations).
