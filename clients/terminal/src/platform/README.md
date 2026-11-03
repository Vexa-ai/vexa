# platform

The client substrate: dependency injection (`createServiceId`/`useService`), observable `createStore`/`useStore`, and the `Command`/`ContextKey` services. Views consume injected services and stores — never reaching across surfaces (VSCode-style DI + service layer).

It also holds the vocabulary the surfaces share, as constants only: `events.ts` (the window events one
surface dispatches and another listens for) and `turnMarks.ts` (the marks and fixed texts a composed chat
turn carries). They live here so that a listener never imports the surface that dispatches; this folder
imports no other folder of `src/`.

It also holds the app-wide facts every layer reads, each self-contained: `mode.ts` (the build-time
terminal profile — full · meetings · minutes), `session.ts` (the auth-failure signal), `analytics.ts`
(GA events), `theme.ts` (the light/dark theme on `<html>`), and the URL shapes `workspaceRoute.ts` and
`meetingRoute.ts`. They lived in `app/`, which made every folder that read them import the composition
root that imports them back; here they sit below every folder that needs them.

`navigate.ts` is the one way a URL that came from data (a notification, a reference, a claim's
arrival) becomes a navigation: `navigateWithin` follows it only when it stays on this origin, and
lands on `/` otherwise — never a `javascript:` URL, never another host.

`layout.ts` is the `LayoutService` (which list is active, which tabs are open in the centre). The
workbench shell renders it; surfaces, the canvas and minutes open tabs through it, so it sits here,
below all of them.

`accountStorage.ts` scopes every `vexa.*` browser storage key to the signed-in account at the
Storage seam (`accountScopeInstall.ts` is the composition root's first import; AuthGate adopts the
account `/api/auth/me` validated). Another account's open workspace, pending meeting or layout is
never applied, and unscoped keys from before are cleared.

`features.ts` is the feature switches (#1783): `GET /api/features` read once, `useFeature(name)` /
`featureOn(name)` for the entry points of built-but-unproven features. It fails closed: a name is
hidden until the answer says it is on, and a name the server does not answer is off. Every name read
is in `SWITCHABLE` and registered server-side (`__tests__/featureRegistry.test.ts`). The vitest setup
(`../../vitest.setup.ts`) renders every `SWITCHABLE` name on.
