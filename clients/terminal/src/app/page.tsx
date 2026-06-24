/**
 * Scaffold home — renders the prototype full-bleed (the "strangler" starting point).
 *
 * The prototype (public/prototype.html) is the interaction + design source of truth. As real views
 * are extracted into React (reusing the @vexa/dash-* bricks for meeting/transcript/chat, plus new
 * terminal modules for tasks/routines/email/calendar), they replace slices of this iframe until it
 * is gone. See README.md and docs/BACKEND.md for the plan.
 */
export default function Home() {
  return (
    <iframe
      src="/prototype.html"
      title="Vexa Terminal prototype"
      style={{ position: "fixed", inset: 0, width: "100%", height: "100%", border: "none" }}
    />
  );
}
