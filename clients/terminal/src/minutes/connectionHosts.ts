/** M3: where a secret entered in the Connections panel is sent, made the thing the person reads.
 *
 * A prepared setup can come from the agent, and the agent reads third-party text (mail, calendar,
 * documents). So the panel shows each destination host as a dominant element of the form, warns
 * when it is not a known provider, and on the first save to a host the person has not approved asks
 * them to type it. Nothing the setup itself names (its documentation URL included) makes a host
 * recognised: the setup may be the agent's. The broker refuses the save without every typed host,
 * so the check does not rest on this file alone. */

/** Hosts of widely used APIs. A warning aid, not an allow-list: an unknown host can be saved once
 *  the person types it. */
export const KNOWN_PROVIDER_HOSTS = [
  'api.telegram.org', 'api.github.com', 'api.openai.com', 'api.anthropic.com', 'api.notion.com',
  'slack.com', 'api.linear.app', 'api.stripe.com', 'api.hubapi.com', 'api.airtable.com',
  'api.todoist.com', 'discord.com', 'api.trello.com', 'graph.microsoft.com', 'www.googleapis.com',
  'oauth2.googleapis.com', 'accounts.google.com', 'login.microsoftonline.com', 'api.intercom.io',
  'api.sendgrid.com', 'api.twilio.com', 'api.mailgun.net', 'api.ouraring.com', 'api.zoom.us',
];

export function hostOf(url: string | undefined | null): string {
  if (!url) return '';
  try { return new URL(url.replace('{secret}', 'x')).hostname.toLowerCase(); } catch { return ''; }
}

/** True only for a known provider API, by exact host. */
export function hostIsRecognised(host: string): boolean {
  return !!host && KNOWN_PROVIDER_HOSTS.includes(host);
}

/** A first save to this host needs the person to type it back. `approvedHost` is the broker's
 *  `approved_host`: the hosts last approved, space-separated. */
export function needsConfirmation(host: string, approvedHost?: string): boolean {
  return !!host && !(approvedHost || '').toLowerCase().split(/\s+/).includes(host);
}
