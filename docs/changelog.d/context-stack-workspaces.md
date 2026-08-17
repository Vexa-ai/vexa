- **The context stack** — the four layers a product-mode agent turn composes (global · group ·
  personal · user-system) now exist as a module: workspace identity (an address that meetings are
  invited to, a name that names the bot), membership with owner/member roles, an explicit policy
  field that routes every context delta, a proposal queue an owner triages, and a write-only
  surface for workspace secrets. Group writes land as proposals and never as direct writes; no
  machine can accept one. See [The context stack](/core/context-stack).
