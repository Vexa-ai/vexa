/** Brand — the ONE place the product's name, mark, and accent colour are decided.
 *
 *  Every user-visible occurrence of the product name, the logo `src`, and the accent token reads
 *  from here, so a deployment rebrands by setting three build args instead of editing source. That
 *  matters because this tree tracks an upstream: a find-and-replace fork conflicts on every one of
 *  those files at every merge, while an indirection conflicts on none of them.
 *
 *  NEXT_PUBLIC_* is inlined by `next build`, so these are BUILD-time knobs (pass them as
 *  --build-arg; see clients/terminal/Dockerfile). Unset ⇒ the defaults below, i.e. an unconfigured
 *  build is byte-identical to the pre-brand one.
 *
 *  NOT in scope here: container/image names, npm package names, git author identity, FastAPI
 *  titles. Those are internal identifiers no user sees, and one of them (the git author) is read
 *  back by a commit filter in the agent domain, so renaming it silently misclassifies workspace
 *  history. Rebranding them buys nothing and risks that.
 */
/** A colour is emitted verbatim into a `<style>` block, so anything that could end the declaration
 *  or the tag is rejected rather than escaped — a build arg is operator-supplied, but a typo that
 *  silently breaks every stylesheet rule after it is the likelier failure. Empty ⇒ no override. */
function cssColor(raw: string | undefined): string {
  const v = (raw ?? "").trim();
  return v && /^[#a-zA-Z0-9(),.%\s/-]+$/.test(v) && v.length <= 64 ? v : "";
}

/** A font-family list is emitted into the same `<style>` block, so it takes the same treatment as
 *  a colour: quotes and commas are legal, anything that could close the declaration is not. */
function fontStack(raw: string | undefined): string {
  const v = (raw ?? "").trim();
  return v && /^[a-zA-Z0-9\s,'"()._-]+$/.test(v) && v.length <= 200 ? v : "";
}

export const BRAND = {
  /** Product name, as it appears in copy: "<name> Terminal", "Continue to <name>". */
  name: process.env.NEXT_PUBLIC_BRAND_NAME?.trim() || "Vexa",
  /** Accent colour. Painted onto `--brand-accent`; globals.css derives --accent and --accentbg
   *  from it, in both themes. Any CSS colour. */
  accent: cssColor(process.env.NEXT_PUBLIC_BRAND_ACCENT),
  /** Accent for the LIGHT theme. A brand colour picked to sit on a dark UI is usually too light
   *  to read on white — the shipped palette carries a darker twin for exactly this reason. Unset
   *  ⇒ `accent` is used in both themes, which is right for a mid-tone brand and wrong for a
   *  bright one, so set it whenever the accent fails contrast on white. */
  accentLight: cssColor(process.env.NEXT_PUBLIC_BRAND_ACCENT_LIGHT),
  /** Text colour painted ON a solid accent fill (buttons, badges). Set it when the accent is dark
   *  enough that the default near-black would be unreadable on it — the accent alone cannot tell
   *  us that. Unset ⇒ each theme's own default. */
  onAccent: cssColor(process.env.NEXT_PUBLIC_BRAND_ON_ACCENT),
  /** Logo URL — anything the browser can load from this origin (put the file in public/). */
  logo: process.env.NEXT_PUBLIC_BRAND_LOGO_URL?.trim() || "/vexa-logo.svg",
  /** Body font stack. A full CSS font-family list, e.g. `"Helvetica Neue", Helvetica, Arial,
   *  sans-serif`. Only the UI/body face — the monospace face is functional, not brand, and
   *  transcripts and code depend on its alignment. */
  font: fontStack(process.env.NEXT_PUBLIC_BRAND_FONT),
} as const;
