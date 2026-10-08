/** M3: where a secret entered in the Connections panel is sent, made the thing the person reads.
 *
 * A prepared setup can come from the agent, and the agent reads third-party text (mail, calendar,
 * documents). So the panel shows the destination host as the dominant element of the form, warns
 * when it does not belong to a known provider or to the service's own documentation, and on the
 * first save to a host the person has not approved asks them to type it. The broker refuses that
 * save without the typed host, so the check does not rest on this file alone. */

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

/** The last two DNS labels: enough to tell `api.example.com` belongs with `docs.example.com`. */
function site(host: string): string {
  return host.split('.').slice(-2).join('.');
}

/** True when the host is a known provider API, or shares its site with the documentation URL the
 *  setup names. */
export function hostIsRecognised(host: string, documentationUrl?: string): boolean {
  if (!host) return false;
  if (KNOWN_PROVIDER_HOSTS.includes(host)) return true;
  const docs = hostOf(documentationUrl);
  return !!docs && site(docs) === site(host);
}

/** A first save to this host needs the person to type it back. */
export function needsConfirmation(host: string, approvedHost?: string): boolean {
  return !!host && host !== (approvedHost || '').toLowerCase();
}
