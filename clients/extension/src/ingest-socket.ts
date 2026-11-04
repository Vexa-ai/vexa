/**
 * How the capture.v1 ingest WebSocket is opened — where the API key goes.
 *
 * A browser `WebSocket` cannot set a header, so the key rides the subprotocol list the
 * gateway's ws.v1 § Connect defines (core/gateway/.../ws_auth.py):
 *
 *     new WebSocket(url, ['vexa.v1', 'vexa.key.' + key])
 *
 * It then travels in `Sec-WebSocket-Protocol`, a request header — never in the URL, so no
 * access log, proxy log or browser history holds it. The server answers `vexa.v1`.
 * The URL carries only the meeting identity and the language. Pure: no chrome, no socket.
 */

export const SUBPROTOCOL = 'vexa.v1';
export const KEY_SUBPROTOCOL_PREFIX = 'vexa.key.';

export interface IngestSocket {
  url: string;
  /** The subprotocol list to open with; empty when there is no key. */
  protocols: string[];
}

export function ingestSocket(
  ingestUrl: string,
  meeting: { platform: string; nativeMeetingId: string },
  language: string,
  apiKey: string,
): IngestSocket {
  const qs = new URLSearchParams({
    platform: meeting.platform,
    native_meeting_id: meeting.nativeMeetingId,
    language,
  });
  const key = (apiKey || '').trim();
  return {
    url: `${ingestUrl}?${qs.toString()}`,
    protocols: key ? [SUBPROTOCOL, KEY_SUBPROTOCOL_PREFIX + key] : [],
  };
}
