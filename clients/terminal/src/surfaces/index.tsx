/**
 * Surfaces barrel — importing this registers every surface (a load-time side effect).
 *
 * In layout v2 a surface registers some mix of: a LEFT list (`registerList`), a CENTER tab-kind
 * (`registerTab`), a RIGHT context-kind (`registerContext`), and /-skill commands (`registerCommand`).
 * The structured shell renders from those registries — adding a surface is a new file + a barrel import,
 * never a shell edit (P2/P6).
 */
import "./chat";       // tab-kind "chat" + /-skills
import "./sessions";   // list "sessions" (→ opens chat tabs)
import "./entities";   // context "entity" (the KG entity card) + EntityList
import "./meeting";    // list "meetings" + tab-kind "meeting" + context "transcript"
import "./workspace";  // list "files" (+ git) + tab-kind "doc" + context "doc-context"
import "./routines";   // list "routines" + tab-kind "routines" (the board)
// tasks deferred — surfaces as quick-action cards in chat later (see roadmap)
