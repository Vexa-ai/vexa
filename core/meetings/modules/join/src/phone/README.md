# join/src/phone — dial-in addresses (no join flow)

The one platform in this layer's vocabulary that **does not join anything**. A conference
speakerphone in a room calls a number and the call IS the meeting — there is no page, so
`joinMeeting` refuses `platform: "phone"` by name and the audio is captured by the phone adapter in
`@vexa/bot` instead. What lives here is the platform's *address* logic, kept beside the join flows
because this is where the platform vocabulary is defined.

`link.ts` parses `tel:` / `sip:` / `sips:` URIs to a canonical dial-in address (`parsePhoneTarget`)
and mints the per-call `native_meeting_id` from an address plus a call discriminator
(`phoneNativeMeetingId`) — the address is what you dial, the discriminator is which call it was, and
a DID hosts every call a room ever makes, so the address alone can never key a meeting.
`link.test.ts` pins the parse table, the refusals, and the feature flag's negative control.

Gated on `VEXA_PHONE_PLATFORM=1`; unset, nothing here recognizes anything and the four browser
platforms are unchanged. Pure string logic — no Playwright, no page, Node builtins only. Design:
[`docs/adr/0036-dial-in-bridge-phone-platform.md`](../../../../../../docs/adr/0036-dial-in-bridge-phone-platform.md).
