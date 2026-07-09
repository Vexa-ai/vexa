/**
 * Surfaces barrel — importing this registers every surface (a load-time side effect).
 *
 * In layout v2 a surface registers some mix of: a LEFT list (`registerList`), a CENTER tab-kind
 * (`registerTab`), and /-skill commands (`registerCommand`).
 * The structured shell renders from those registries — adding a surface is a new file + a barrel import,
 * never a shell edit (P2/P6).
 *
 * Meetings-only mode (NEXT_PUBLIC_TERMINAL_MODE=meetings — see ../app/mode.ts): the agent surfaces
 * (chat, sessions, workspace, routines, tasks) gate their OWN register* calls on `!meetingsOnly()`,
 * so in that mode only meetings + canvas + tokens contribute lists/tabs/commands. The imports stay
 * unconditional (ESM imports are static); a gated module simply registers nothing.
 */
import "./chat";       // right-rail Chat export + /-skills          (commands gated in meetings mode)
import "./sessions";   // list "sessions" (→ focuses right-rail chat) (gated in meetings mode)
import "./entities";   // EntityList helpers
import "./meeting";    // list "meetings" + tab-kind "meeting"
import "./meetingPrep"; // tab-kind "meetingPrep" — a planned meeting's prep/share hub
import "./today";      // tab-kind "today" — the Meetings click target (the user's day)
import "./canvas";     // tab-kind "canvas" + command "Open Meeting Canvas"
import "./workspace";  // list "files" (+ git) + tab-kind "doc"       (gated in meetings mode)
import "./workspaceManage"; // tab-kind "workspace" — the manage hub  (gated in meetings mode)
import "./routines";   // list "routines" + tab-kind "routines"       (gated in meetings mode)
import "./tokens";     // token panels (rendered inside Settings; no list of its own anymore)
import "./settings";   // tab-kind "settings" — the footer-gear hub (calendar · tokens · github · account)
import "./admin";      // HIDDEN admin infra panel — registers only after /api/admin/me confirms (gated in meetings mode)
// tasks deferred — surfaces as quick-action cards in chat later (see roadmap)
