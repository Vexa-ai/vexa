/** policyDoc — `_global/POLICIES.md` rendered as what it is: a set of ANSWERS with their reasons.
 *
 *  Founder, 2026-09-06: *"we need to get to policy primitives, see how they compose and what
 *  effects pros and cons each has — that's the choice we will let the global admin take."* The file
 *  carries the answers in its front matter and the reasoning in its body, one rule per section with
 *  three lenses on each — adoption, security, adversarial — because a switch whose consequences
 *  live somewhere else is a switch nobody can weigh.
 *
 *  Every renderer before this one STRIPPED front matter as "metadata for the agent, never body
 *  copy" (`MdxDoc.stripFrontmatter`). For this one file that is exactly backwards: the front matter
 *  IS the content — it is what the deployment currently answers — and the prose under it is the
 *  argument for each answer. So the block is parsed rather than discarded, rendered as the rule
 *  list at the top of the page, and each row carries the default and the three lenses lifted out of
 *  that rule's own section.
 *
 *  NOTHING HERE KNOWS THE RULES. There is no table of keys, no list of defaults, no copy of the
 *  lens text: every word comes out of the file being rendered. A rule added in `POLICIES.md` and
 *  read by the flows appears here with its default and its three lenses without this file being
 *  touched — and a rule whose section is missing degrades to a row with a value and a link, which
 *  is what the page would have shown anyway.
 *
 *  IT IS RECOGNISED BY WHAT IT DECLARES, NOT BY ITS PATH (`kind: policies`). A page is not a policy
 *  page because somebody named the file right — the seeded file says what it is, in a line the
 *  renderer and the flows both read.
 */
import type { CSSProperties, ReactNode } from "react";

export const POLICY_KIND = "policies";

/** The kind the page's own act registers under (`contributions`), so ui-kit never imports a
 *  shell — the same seam `TRANSCRIPT_WIDGET_KIND` uses one file away. A build with nothing
 *  registered renders the rules and no act (Vexa-ai/vexa#1627). */
export const POLICY_ACT_KIND = "policies-act";

export type Attr = [string, string];

/** `key: value` lines between the opening and closing fence, plus the body under it.
 *  A file with no fence is all body; a fence that never closes is not front matter. The same two
 *  laws the Python side keeps (`flows_steps/policies.front_matter`), because the same file is read
 *  by both and a disagreement about what a fence is would be invisible until it mattered. */
export function splitFrontmatter(md: string): { attrs: Attr[]; body: string } {
  const src = md ?? "";
  if (!src.startsWith("---")) return { attrs: [], body: src };
  const end = src.indexOf("\n---", 3);
  if (end === -1) return { attrs: [], body: src };
  const after = src.indexOf("\n", end + 1);
  const head = src.slice(src.indexOf("\n") + 1, end);
  const body = after === -1 ? "" : src.slice(after + 1).replace(/^\s+/, "");
  const attrs: Attr[] = [];
  for (const line of head.split("\n")) {
    if (!line.trim() || line.trim().startsWith("#")) continue;
    const at = line.indexOf(":");
    if (at <= 0) continue;
    attrs.push([line.slice(0, at).trim().toLowerCase(), line.slice(at + 1).trim().replace(/^["']|["']$/g, "")]);
  }
  return { attrs, body };
}

export function declaredKind(attrs: Attr[]): string {
  return attrs.find(([k]) => k === "kind")?.[1] ?? "";
}

export type RuleDoc = {
  title?: string;
  fallback?: string;      // "Default `on`." — the shipped answer, read off the page
  adoption?: string;
  security?: string;
  adversarial?: string;
};

const LENS_RUN = /\*\*(Adoption|Security|Adversarial)\.\*\*\s*([\s\S]*?)(?=\*\*(?:Adoption|Security|Adversarial)\.\*\*|$)/g;

/** What each rule's own section says: its heading, its default, and its three lenses.
 *
 *  Sections are found by the anchor the page emits before each one (`<a id="key"></a>`), which is
 *  the same anchor the generated flow pages link to — so if a rule is reachable from a flow page it
 *  is reachable here, by construction rather than by two conventions agreeing. */
export function policyRuleDocs(body: string): Record<string, RuleDoc> {
  const out: Record<string, RuleDoc> = {};
  const parts = (body ?? "").split(/<a id="([a-z0-9_]+)"><\/a>/);
  for (let i = 1; i < parts.length; i += 2) {
    const key = parts[i];
    const section = parts[i + 1] ?? "";
    const doc: RuleDoc = {};
    const heading = section.match(/^\s*###\s+(.+?)\s*$/m);
    if (heading) doc.title = heading[1].replace(/`/g, "").replace(/^[\w_/ ]+—\s*/, "").trim();
    const fallback = section.match(/\*\*Defaults?[^*]*\*\*/);
    if (fallback) doc.fallback = fallback[0].replace(/\*\*/g, "").replace(/\.$/, "").trim();
    for (const m of section.matchAll(LENS_RUN)) {
      const text = m[2].replace(/\*\*/g, "").replace(/\s+/g, " ").trim().replace(/\s*$/, "");
      (doc as Record<string, string>)[m[1].toLowerCase()] = text;
    }
    out[key] = doc;
  }
  return out;
}

// ── the rule list ────────────────────────────────────────────────────────────────────────────
// Not a settings screen. Nothing here is editable: `_global` is admin-only and every change to it
// is a commit with an author, so the surface that changes a rule is the file, and this is the
// surface that lets somebody understand one before they do.

const ON = new Set(["on", "true", "yes", "1"]);
const OFF = new Set(["off", "false", "no", "0"]);

const chip = (color: string, bg: string): CSSProperties => ({
  display: "inline-flex", alignItems: "center", gap: 5, background: bg, border: `1px solid ${color}`,
  borderRadius: 999, padding: "1px 9px", color, fontSize: 12, fontWeight: 500, lineHeight: 1.6,
  whiteSpace: "nowrap",
});

function Value({ value }: { value: string }): ReactNode {
  const v = value.trim();
  if (!v) return <span style={{ color: "var(--t3)", fontSize: 12 }}>unset — the default applies</span>;
  if (ON.has(v.toLowerCase())) return <span style={chip("var(--green)", "transparent")}>on</span>;
  if (OFF.has(v.toLowerCase())) return <span style={chip("var(--t3)", "transparent")}>off</span>;
  return <span style={{ fontFamily: "var(--mono)", fontSize: 12, color: "var(--t1)" }}>{v}</span>;
}

function Lens({ label, text }: { label: string; text?: string }): ReactNode {
  if (!text) return null;
  return (
    <div style={{ display: "flex", gap: 8, alignItems: "baseline", lineHeight: 1.5 }}>
      <span style={{ color: "var(--t3)", fontSize: 11, textTransform: "uppercase", letterSpacing: 0.4, width: 78, flex: "none" }}>{label}</span>
      <span style={{ color: "var(--t2)", fontSize: 12.5, minWidth: 0 }}>{text}</span>
    </div>
  );
}

/** Everything the front matter answers, each row carrying what that answer costs. */
export function PolicyRules({ attrs, body, act }: { attrs: Attr[]; body: string; act?: ReactNode }): ReactNode {
  const docs = policyRuleDocs(body);
  const profile = attrs.find(([k]) => k === "profile")?.[1];
  const rules = attrs.filter(([k]) => k !== "kind" && k !== "profile");
  return (
    <div data-policy-rules="" style={{ border: "1px solid var(--line)", borderRadius: 10, background: "var(--panel)", padding: "12px 14px", margin: "0 0 16px" }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 10, marginBottom: 4 }}>
        <span style={{ fontSize: 14, fontWeight: 600, color: "var(--t1)" }}>What this deployment answers</span>
        {profile ? <span style={chip("var(--blue)", "var(--bluebg)")}>{profile}</span> : null}
        {/* THE WAY TO CHANGE THEM, BESIDE WHAT THEY ARE (Vexa-ai/vexa#1627). The profile chip says
            where this deployment stands; the act is how somebody who does not like that answer
            arrives at a different one — five questions about their own risks, then a recommendation
            with the reasoning from the sections below. It is a slot, not an import: what fills it is
            registered by a shell, and a build with nothing registered simply shows the rules. */}
        {act ? <><span style={{ flex: "1 1 0%" }} />{act}</> : null}
      </div>
      <div style={{ color: "var(--t3)", fontSize: 12, lineHeight: 1.5, marginBottom: 10 }}>
        Each rule is written out below with what it changes, what it buys, what it costs and what a
        hostile person does with it. Changing one is an edit to this file, and every edit is a commit
        with an author.
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        {rules.map(([key, value]) => {
          const doc = docs[key];
          return (
            <div key={key} data-policy-rule={key} style={{ borderTop: "1px solid var(--line)", paddingTop: 9 }}>
              <div style={{ display: "flex", gap: 10, alignItems: "baseline", flexWrap: "wrap" }}>
                <a href={`#${key}`} style={{ fontFamily: "var(--mono)", fontSize: 12.5, color: "var(--t1)", textDecoration: "none", borderBottom: "1px dotted var(--line2)" }}>{key}</a>
                <Value value={value} />
                {doc?.fallback ? <span style={{ color: "var(--t3)", fontSize: 11.5 }}>{doc.fallback}</span> : null}
              </div>
              {doc?.title ? (
                <div style={{ color: "var(--t2)", fontSize: 12.5, lineHeight: 1.5, margin: "3px 0 5px" }}>{doc.title}</div>
              ) : null}
              <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
                <Lens label="adoption" text={doc?.adoption} />
                <Lens label="security" text={doc?.security} />
                <Lens label="adversarial" text={doc?.adversarial} />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── the view-source fold on a generated flow page ────────────────────────────────────────────
// The founder asked whether we can show them the Python. The answer is yes, as the APPENDIX of a
// page written for them — folded shut, so the page reads as prose and the code is one click away
// for the person who wants it.
//
// A REGISTERED COMPONENT, NOT RAW `<details>`. Both are in the compile's tag allow-list and both
// compile, but a lowercase tag written literally in the source does NOT resolve through MDX's
// component map — measured, not assumed: the `<pre>` markdown produced inside the fold came out
// styled and the `<details>` around it came out bare. The closed registry is the only surface where
// this page's own vocabulary can be given a shape, so the generator emits `<ViewSource>` and this
// is what it means.

export function ViewSource({ step, children }: { step?: string; children?: ReactNode }): ReactNode {
  return (
    <details data-view-source={step ?? ""} style={{ border: "1px solid var(--line)", borderRadius: 8, background: "var(--panel)", margin: "8px 0", padding: "6px 10px" }}>
      <summary style={{ cursor: "pointer", color: "var(--t2)", fontSize: 12.5, userSelect: "none" }}>
        view source{step ? <> — <code style={{ fontFamily: "var(--mono)" }}>{step}</code></> : null}
      </summary>
      {children}
    </details>
  );
}

// ── a generated flow page, headed by what it declares ─────────────────────────────────────────
// Founder, 2026-09-06: *"flows live in global, right?"* (Vexa-ai/vexa#1626). `_global/flows/*.md`
// is one page per flow, generated from the code that runs it (#1615): what fires it, the steps in
// order, what each one mails, and the rules it honours. Every renderer stripped its front matter,
// so the reader met the prose and had to walk down into a table to learn what the page even was.
//
// SAME RULE AS THE POLICY PAGE, AND THE SAME REASON: the block is the page's own declaration of
// what it is, so it is rendered rather than discarded — here as one header line above the prose,
// because a flow page's front matter is a summary and the policy page's IS the content.
//
// AND LIKE `PolicyRules`, NOTHING HERE KNOWS THE FLOWS. There is no list of triggers, no table of
// steps, no copy of a rule name: every word comes out of the file being rendered. A flow added to
// the code gets its page from `make flow-pages` and its header from this, with nothing edited here.

export const FLOW_KIND = "flow";

/** The rules a flow page says it honours, read off its own summary table.
 *
 *  The generator writes them as links into the policy page (``[`key`](../POLICIES.md#key)``), and
 *  that anchor is the one `policyRuleDocs` above splits on — so the two halves of #1615 agree by
 *  construction: a rule reachable from a flow page is a rule with a section to reach. A flow that
 *  honours none writes `none` in that cell, which yields an empty list, not a missing row. */
export function flowRules(body: string): string[] {
  const row = /^\|\s*\*\*rules it honours\*\*\s*\|(.*?)\|[ \t]*$/m.exec(body ?? "");
  if (!row) return [];
  const seen = new Set<string>();
  for (const m of row[1].matchAll(/POLICIES\.md#([a-z0-9_]+)/g)) seen.add(m[1]);
  return [...seen];
}

const flowBit = (label: string, value: ReactNode): ReactNode => (
  <span style={{ display: "inline-flex", alignItems: "baseline", gap: 5, minWidth: 0 }}>
    <span style={{ color: "var(--t3)", fontSize: 11, textTransform: "uppercase", letterSpacing: 0.4 }}>{label}</span>
    <span style={{ color: "var(--t2)", fontSize: 12.5, minWidth: 0 }}>{value}</span>
  </span>
);

const mono: CSSProperties = { fontFamily: "var(--mono)", fontSize: 12, color: "var(--t1)" };

/** Trigger · steps · rules, on one line, from the page's own front matter and summary table.
 *
 *  A field the page does not declare is simply absent — the header degrades a bit at a time rather
 *  than rendering `undefined`, which is the same rule the policy rows follow. */
export function FlowHeader({ attrs, body }: { attrs: Attr[]; body: string }): ReactNode {
  const at = (k: string) => attrs.find(([key]) => key === k)?.[1]?.trim() ?? "";
  const flow = at("flow");
  const trigger = at("trigger");
  const steps = at("steps");
  const version = at("version");
  const generated = at("generated");
  const rules = flowRules(body);
  return (
    <div data-flow-header={flow} style={{ border: "1px solid var(--line)", borderRadius: 10, background: "var(--panel)", padding: "10px 14px", margin: "0 0 16px" }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap", rowGap: 4 }}>
        <span style={chip("var(--blue)", "var(--bluebg)")}>flow</span>
        {flow ? <span style={{ ...mono, fontSize: 13, fontWeight: 600 }}>{flow}</span> : null}
        {trigger ? flowBit("trigger", <code style={mono}>{trigger}</code>) : null}
        {steps ? flowBit("steps", steps) : null}
        {version ? flowBit("version", version) : null}
        {flowBit("rules", rules.length
          ? <span>{rules.map((r, i) => (
              <span key={r} data-flow-rule={r}>{i ? ", " : ""}<code style={mono}>{r}</code></span>
            ))}</span>
          : <span style={{ color: "var(--t3)" }}>none</span>)}
      </div>
      {generated ? (
        <div style={{ color: "var(--t3)", fontSize: 11.5, lineHeight: 1.5, marginTop: 6 }}>{generated}</div>
      ) : null}
    </div>
  );
}

// ── a step that does not exist yet ────────────────────────────────────────────────────────────
// Founder, 2026-09-06: *"we want to be able to write flows for the global chat as we like."*
// (Vexa-ai/vexa#1639.) A flow is composed from step names the image already carries — so a sentence
// that needs something no step does has, until now, had nowhere to go: the agent could only refuse.
//
// It writes the step out instead, as a page under `_global/flows/proposals/`: the Python in this
// repo's own step shape, its docstring, the flow that would use it and the tests it needs. NOTHING
// EXECUTES IT and nothing can — `flows_submit` validates every step name against the deployed
// vocabulary at submission, and this name is not in it. The page exists to be READ, and then sent.
//
// SO THE ACT IS THE POINT OF THE HEADER, unlike a flow page's, which has nothing to decide. It is
// the same slot the policy page uses, for the same reason: ui-kit must not import a shell, so what
// fills it is registered under a kind, and a build with nothing registered renders the page and no
// act — which is honest, because the act starts a conversation and such a build has no chat.

export const PROPOSAL_KIND = "proposal";

/** The kind the proposal page's own act registers under (`contributions`) — `POLICY_ACT_KIND` one
 *  page along, and separate from it because they are two different acts on two different pages. */
export const PROPOSAL_ACT_KIND = "proposal-act";

/** What it is, what it would be used by, and whether it has been sent — from the page's own front
 *  matter, like every other header here. Nothing in this file knows what a step is. */
export function ProposalHeader({ attrs, act }: { attrs: Attr[]; act?: ReactNode }): ReactNode {
  const at = (k: string) => attrs.find(([key]) => key === k)?.[1]?.trim() ?? "";
  const step = at("step");
  const forFlow = at("for-flow");
  const trigger = at("trigger");
  const status = at("status");
  return (
    <div data-proposal-header={step} style={{ border: "1px solid var(--line)", borderRadius: 10, background: "var(--panel)", padding: "10px 14px", margin: "0 0 16px" }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap", rowGap: 4 }}>
        <span style={chip("var(--t3)", "transparent")}>proposal</span>
        {step ? <span style={{ ...mono, fontSize: 13, fontWeight: 600 }}>{step}</span> : null}
        {forFlow ? flowBit("for", <code style={mono}>{forFlow}</code>) : null}
        {trigger ? flowBit("trigger", <code style={mono}>{trigger}</code>) : null}
        {act ? <><span style={{ flex: "1 1 0%" }} />{act}</> : null}
      </div>
      <div style={{ color: "var(--t3)", fontSize: 11.5, lineHeight: 1.5, marginTop: 6 }}>
        {status || "needs code — never executed"}. This deployment does not carry this step; nothing
        here runs, and nothing will until somebody writes it.
      </div>
    </div>
  );
}

// ── which header a page gets ──────────────────────────────────────────────────────────────────

/** THE HEADER A PAGE DECLARES, or nothing. Recognised by `kind:` and never by path — a page is not
 *  the policy page because somebody named the file right, and a page in `flows/` that does not say
 *  it is a flow is a page somebody wrote there.
 *
 *  One function, so the renderer has one branch instead of one per kind, and so a new kind is a
 *  line here rather than an edit inside `MdxDoc`. */
export function docHeader(attrs: Attr[], body: string, act?: ReactNode): ReactNode {
  const kind = declaredKind(attrs);
  // `act` is the page's own — the way to change the rules beside what they are on the policy page
  // (Vexa-ai/vexa#1627), the way to send a step to the developers on a proposal (#1639). A flow
  // page has nothing to decide, so it is offered nothing. `MdxDoc` chooses WHICH act by the same
  // kind, so a page can never be handed the other page's control.
  if (kind === POLICY_KIND) return <PolicyRules attrs={attrs} body={body} act={act} />;
  if (kind === PROPOSAL_KIND) return <ProposalHeader attrs={attrs} act={act} />;
  if (kind === FLOW_KIND) return <FlowHeader attrs={attrs} body={body} />;
  return null;
}
