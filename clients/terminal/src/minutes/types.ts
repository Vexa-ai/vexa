/** The shell's selection model: which room, which chat — the two things the whole screen follows. */
export type Sel = {
  kind: "personal" | "shared" | "org" | "meeting";
  id: string;
  label: string;
  session?: string;      // explicit agent session (extra chats); derived from kind otherwise
  chatLabel?: string;
};
export type Page = { path: string; slug?: string; label: string };
export type View = "meetings" | "rooms";
