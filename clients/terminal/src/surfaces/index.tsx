/**
 * Surfaces barrel — importing this registers every surface (a load-time side effect).
 *
 * In layout v2 a surface registers some mix of: a LEFT list (`registerList`), a CENTER tab-kind
 * (`registerTab`), and /-skill commands (`registerCommand`).
 * The structured shell renders from those registries — adding a surface is a new file + a barrel import,
 * never a shell edit (P2/P6).
 */
import "./chat";       // right-rail Chat export + /-skills
import "./sessions";   // list "sessions" (→ focuses right-rail chat)
import "./entities";   // EntityList helpers
import "./meeting";    // list "meetings" + tab-kind "meeting"
import "./canvas";     // tab-kind "canvas" + command "Open Meeting Canvas"
import "./workspace";  // list "files" (+ git) + tab-kind "doc"
import "./routines";   // list "routines" + tab-kind "routines" (the board)
// tasks deferred — surfaces as quick-action cards in chat later (see roadmap)
