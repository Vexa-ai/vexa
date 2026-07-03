# Terminal — frontend architecture (the workbench)

A VSCode-workbench-modeled, contribution-driven architecture. Every surface is a self-contained
module that plugs into a stable shell by **registering**, never by editing the shell. The whole app
rides one spine — `UnitRuntimeService` — the frontend mirror of the backend's single *agent runtime
unit*. Goal: surfaces are pluggable; MVP stages are **purely additive**.

## Principles
1. **Shell knows nothing about surfaces.** The workbench renders *parts* (slots); surfaces *contribute*
   views into slots via a registry. Adding a surface = a `registerSurface(...)` call, zero shell edits.
2. **Everything is a service behind an interface.** Platform + domain services are interfaces;
   implementations are injected. The `@vexa/dash-*` bricks are the injected adapters.
3. **One spine.** Chat, Live, Workspace are the *same* `UnitRuntimeService` with different
   trigger/context — the frontend mirror of the backend's one primitive.
4. **Observable stores, not Zustand.** State follows the `dash-meeting-state` shape
   (`createX({...ports}) → {getState, subscribe}`), port-injected, framework-agnostic; React subscribes
   via `useStore` (`useSyncExternalStore`).
5. **Report state from evidence; fail loud.** A typed `AsyncState<T>` (loading/empty/error/populated)
   is the floor for every loading view (`<StatefulView>`); `live` is earned by an observed frame; a
   non-200 surfaces as `error`, never a fake empty (DF2/DF3).

## Directory structure (npm workspaces, brick discipline)
```
clients/terminal/
  src/app/                       # COMPOSITION ROOT ONLY — boots the container, renders <Workbench/>
    page.tsx                     # boots DI container → <Workbench/> (replaces the prototype iframe)
    api/vexa/[...path]/route.ts  # REST proxy (ported from dashboard_new) — api key stays server-side
    api/chat/[...path]/route.ts  # SSE proxy for /api/chat* (agent-api), streamed, key server-side
    api/config/route.ts          # runtime config (dash-config) → {apiUrl, wsUrl, authToken}
  modules/
    @vexa/term-workbench/        # shell, Parts, LayoutService, <StatefulView>, <PartErrorBoundary>
    @vexa/term-platform/         # DI container + useService/useStore; Command, ContextKey, Notification,
                                 #   Config, Keybinding services; the when-clause evaluator
    @vexa/term-contributions/    # the Surface contribution API + ContributionRegistry
    @vexa/term-services/         # ApiClient, Ws, UnitRuntime, Workspace, Auth — INTERFACES + adapters
                                 #   that wrap the @vexa/dash-* bricks
    @vexa/term-ui-kit/           # prototype dark tokens + primitives (Bubble, ProactiveCard, Chip,
                                 #   Pill, ListRow, Seg, CommandPalette, StatusChip, icons)
    @vexa/term-surface-chat/     # MVP0   — one brick per surface; each a self-contained contribution
    @vexa/term-surface-workspace/# MVP1
    @vexa/term-surface-routines/ # MVP2
    @vexa/term-surface-tasks/    # MVP2
    @vexa/term-surface-inbox/    # MVP3
    @vexa/term-surface-calendar/ # MVP3
    @vexa/term-surface-live/     # MVP4   (reuses dash-meeting-state + dash-transcript-viewer + dash-vnc)
    @vexa/term-surface-triage/   # MVP5
    @vexa/term-setup/            # the vertical-templated wizard → Deployment step
```
**Surfaces are packages, not `src/surfaces/*`** — a surface is a concern; a package gives it a front
door (`registerSurface`), an enforced acyclic dep (only `term-contributions` + `term-services` +
`term-ui-kit`, never another surface, never the shell), and a per-brick test. `@vexa/dash-*` bricks are
consumed **only** by `term-services` (adapters) and view bricks (e.g. `dash-transcript-viewer` inside
`term-surface-live`) — surfaces never import a `dash-*` brick for *data*; they go through a service.

## DI (lightweight, no decorators/reflect-metadata)
```ts
export interface ServiceId<T> { readonly _t?: T; readonly id: string }
export function createServiceId<T>(id: string): ServiceId<T>
export interface ServiceContainer { get<T>(id: ServiceId<T>): T; tryGet<T>(id: ServiceId<T>): T | undefined }
export function createContainer(regs: ServiceRegistration<unknown>[]): ServiceContainer
// React bridge (the ONE bridge):
export function ServicesProvider(p: {container; children}): JSX.Element
export function useService<T>(id: ServiceId<T>): T
export function useStore<S>(store: {getState():S; subscribe(cb:(s:S)=>void):()=>void}): S  // useSyncExternalStore
```
The container is plain data; React reaches it via one context holding the container; `get` throws
loudly on an unregistered id (fail-loud). The `<Workbench/>` boundary is `"use client"`; server
components (layout, proxy routes) stay above it.

## Workbench parts (the layout model)
| Part | Prototype element | Contents | Toggle |
|---|---|---|---|
| ActivityBar | left `nav.primary` | primary nav (Live·Chat·Workspace·Inbox·Calendar·Tasks·Routines) | always; icons-only < md |
| PrimarySidebar | `.ctx` + brand/user | per-surface context view (folders, file tree, filters) | `⌘B`; width persisted |
| Main | `.vhead` + `.body` | active surface's main view | always |
| Composer | `.composer` | the `/`-skill command bar (when the surface declares one) | per-surface flag |
| AuxiliaryBar | `aside.right` | right rail (transcript / doc / mail / note) | rail toggle; width persisted |
| Panel | (new) | ws/debug (`dash-ws-event-log`, tool-trace) | hidden by default |
| StatusBar | `.deprow` + topbar chips | deployment chip · connection chip · unit status | status items contributed |

`LayoutService` (observable-store shape) owns geometry: `setActiveSurface`, `togglePart`,
`setPartSize` (drag-resize, persisted via a `LayoutPersistence` port), `applyResponsive(width)` (a
`ResizeObserver` → narrow mode collapses sidebar+aux to overlays). **A surface never sizes itself** — it
declares which slots it fills; the shell owns geometry.

## The Surface contribution API (the heart of additivity)
```ts
export interface SurfaceContribution {
  id: SurfaceId;
  activity?: ActivityItem;            // ActivityBar item (label, icon, order, optional reactive badge)
  views?: ViewContribution[];         // { slot: "main"|"primarySidebar"|"auxiliaryBar"|"panel", component, when? }
  composer?: { enabled: boolean; placeholder?: string; quickChips?: string[] };
  commands?: CommandContribution[];   // incl. '/'-skills (skill?: `/${string}`)
  keybindings?: Keybinding[];
  statusItems?: StatusItemContribution[];
  contextKeys?: ContextKeyDecl[];
  activate?(c: ServiceContainer): void | (() => void);   // optional eager wiring; returns dispose
}
export function registerSurface(s: SurfaceContribution): void   // singleton ContributionRegistry
```
The shell renders entirely off the registry: ActivityBar from `getActivityItems()`, Main/Sidebar/Aux
from `getViews(slot, activeSurface)`, Composer from the active surface, CommandService from
`getCommands()`, StatusBar from `getStatusItems()`. The **only** place that knows the surface list is a
single import array in the composition root (gated per MVP); even it just imports — surface modules run
`registerSurface(...)` as a load-time side-effect.

## UnitRuntimeService — the spine
```ts
export type UnitTrigger =
  | { kind: "message" }                              // Chat
  | { kind: "live-stream"; meeting: MeetingHandle }  // Live
  | { kind: "time-cron"; cron: string }              // Routine (time)
  | { kind: "event"; event: string };                // Routine (event) / Triage
export interface UnitContext { workspaceRef?: string; sessionId?: string; entities?: string[]; [k:string]: unknown }
export type UnitEvent =
  | { type: "message-delta"; text: string; messageId: string }
  | { type: "tool-call"; tool: string; args: unknown; callId: string }
  | { type: "tool-result"; callId: string; ok: boolean; summary: string }
  | { type: "proactive-card"; card: ProactiveCard }
  | { type: "commit"; sha: string; filesChanged: number; message: string }
  | { type: "status"; status: "thinking"|"idle"|"sleeping"|"stopped"|"error" }
  | { type: "error"; code: string; message: string };
export interface UnitRuntimeService {
  spawn(req: { trigger: UnitTrigger; context: UnitContext; plan?: UnitPlan }): Promise<UnitHandle>;
  send(unitId: string, input: { text?: string; action?: { command: string; payload?: unknown } }): void;
  subscribe(unitId: string): Observable<UnitEvent>;   // the spine every surface renders
  resume(sessionId: string): Promise<UnitHandle>; sleep(unitId: string): void; stop(unitId: string): void;
}
```
`createUnitRuntimeService` is the single place that fuses `dash-ws` (`ws.v1` live events) and the
`/api/chat` SSE proxy (turn events) into the `UnitEvent` union keyed by `unitId`. Surfaces never see
SSE/websockets — only `UnitEvent`. Chat spawns `trigger:message`; Live spawns
`trigger:live-stream` (+ drives `dash-meeting-state` for the raw transcript rail); Workspace spawns
`trigger:message` with `context.workspaceRef`; Routines *author* spawn requests. The
**proactive-card → command → `send(action)`** loop is uniform across Live/Triage/Inbox — that
uniformity is what makes those surfaces additive over Chat, not bespoke.

## Command / skill system
`/`-skills are `CommandContribution`s where `skill !== undefined`. The composer is a quick-input
palette over `CommandService.querySkills(input)` (prototype's `renderChips`, formalized): `/` →
filtered skills; non-slash → the surface's `quickChips`; Enter → `execute(skill.id, rest)` or
`UnitRuntimeService.send`. `⌘K` = a global `palette.open` command. Keybindings + menu/skill visibility
gate on **context keys** (`ContextKeyService` + a small VSCode-style when-clause evaluator shipped in
`term-platform` from day one).

## Reuse mapping (`@vexa/dash-*` → service/surface)
`dash-api-client` → **ApiClientService**; `dash-ws` → **WsService** (+ UnitRuntime live events);
`dash-meeting-state` → consumed in `term-surface-live`; `dash-config` → `/api/config` + ConfigService;
`dash-transcript-viewer`/`dash-recording-players`/`dash-vnc-view`/`dash-chat` → view bricks in Live /
Calendar / Panel. New terminal logic (Tasks, Routines, Inbox importance, Calendar grid, the `/`-bar,
setup wizard) lives in `term-surface-*`/`term-services`, talking to backend additions through the
**same** service interfaces — so a backend port changes only an adapter, never a surface.

## Foundation vs per-MVP
- **Foundation (with MVP0):** `term-workbench` + `term-platform` + `term-contributions` +
  `term-services` (Api/Ws/UnitRuntime) + `term-ui-kit` + **`term-surface-chat`**. This *is* the
  substrate every later surface plugs into — not a one-off page.
- **Each later MVP** = one `term-surface-*` package (`registerSurface`) + (where the backend lands a
  new capability) one new adapter behind an existing service interface. No MVP edits the shell, the
  layout, the command system, or a prior surface — the acyclic workspace-dep rule makes "additive
  contribution" the only shape the build accepts.
