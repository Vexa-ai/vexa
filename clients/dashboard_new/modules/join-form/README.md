# @vexa/dash-join-form — the start-bot form VIEW brick

_dashboard_new/ · module · platform select + meeting URL/native-id input + bot name → `onSubmit(CreateBotRequest)`._

A **presentational** React component: props in, DOM out. It renders a platform `<select>`, a meeting
URL / native-id `<input>`, and a bot-name `<input>`. On submit it parses the pasted input into
`(platform, native_meeting_id)` and hands an assembled `CreateBotRequest` to the injected `onSubmit`.

**No store, no fetch, no websocket.** All data is injected; the only output is the `onSubmit` call. This
is the clean modular replacement for the vendored `dashboard/src/components/join/join-form.tsx` (which
was wired straight to `vexaAPI` + a zustand store + `toast` + `localStorage`). Here those couplings are
the caller's job — this brick just produces the request.

Typed by [`@vexa/dash-contracts`](../dash-contracts/): the `platform` field is the contract's `Platform`
union, so the form speaks the exact vocabulary the rest of the dashboard does and never invents platform
strings.

## Props contract

```ts
interface JoinFormProps {
  /** Called with the assembled CreateBotRequest when submitted with valid input. */
  onSubmit: (request: CreateBotRequest) => void;
  /** Pre-fills the bot-name field. Defaults to "Vexa". */
  defaultBotName?: string;
}
```

The `CreateBotRequest` it emits (api.v1 `POST /bots` body; `platform` + `native_meeting_id` are the
floor, the rest only present when supplied):

```ts
interface CreateBotRequest {
  platform: Platform;            // from @vexa/dash-contracts
  native_meeting_id: string;     // parsed out of the URL/id
  passcode?: string;             // pulled from the URL query (Teams `p=`, Zoom `pwd=`)
  meeting_url?: string;          // preserved original URL for white-label / Teams links
  bot_name?: string;             // the trimmed bot-name field, when non-empty
}
```

**Parsing.** `parseMeetingInput(input)` (also exported) turns a pasted link or bare id into
`{ platform, nativeId, passcode?, originalUrl? }`:

| input | → platform | → nativeId |
| --- | --- | --- |
| `https://meet.google.com/abc-defg-hij` or `abc-defg-hij` | `google_meet` | `abc-defg-hij` |
| `https://zoom.us/j/85173157171?pwd=…` or `85173157171` | `zoom` | `85173157171` |
| `https://teams.microsoft.com/l/meetup-join/…?p=…` | `teams` | thread id (+ passcode) |

The platform is detected **from the URL** (canonical when recognized); the `<select>` lets the user
override or classify a bare numeric id. Unrecognized input → inline error, `onSubmit` does NOT fire.

## Surface

`JoinForm` (component) · `parseMeetingInput` (pure URL→id parser) · types `JoinFormProps`,
`CreateBotRequest`, `ParsedMeetingInput`, `Platform`. Front door: [`src/index.ts`](src/index.ts).

## Verify

`npm run build` — `tsc` clean (tsconfig adds `DOM` + `react-jsx`).

`npm test` — the **L4 bulletproof gate**: a real chromium (Playwright) loads
[`e2e/fixtures/join-form.html`](e2e/fixtures/join-form.html), which esbuild-bundles the **real**
`JoinForm` and mounts it with react-dom over golden props. The spec
([`e2e/join-form.spec.ts`](e2e/join-form.spec.ts)) acts as a human: fills the meeting URL
(`https://meet.google.com/abc-defg-hij`), clicks **Start bot**, and asserts the component fired
`onSubmit` with the **parsed** request `{ platform: "google_meet", native_meeting_id: "abc-defg-hij",
bot_name: "Vexa" }`. A second test asserts invalid input does NOT fire `onSubmit` and shows the inline
error. Green-in-Playwright ⇒ green-for-the-human's-browser. (`npx playwright install chromium` if the
browser isn't present.)
