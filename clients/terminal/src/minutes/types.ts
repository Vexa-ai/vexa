/** The shell's selection model: which project, which chat — the two things the screen follows. */
export type Sel = {
  kind: "personal" | "org" | "meeting" | "project";
  id: string;            // project id, or meeting row id
  label: string;
  session?: string;      // explicit agent session (project chats); derived from kind otherwise
  chatLabel?: string;
};
export type Page = { path: string; slug?: string; label: string };
export type View = "meetings" | "projects";
