/** `composeDesktop` answers desktop.v1's `Desktop` (#1783, desktop ADR D1/D2). The composition the
 *  `/api/desktop` route answers is validated against the contract's own schema, cards included, so a
 *  shape drift between the terminal and the contract fails here rather than on a person's home. */
import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { composeDesktop, type CardView, type Desk, type DesktopSections } from "../desktop";

const DIR = join(__dirname, "../../../../../core/agent/contracts/desktop.v1");
const schema = JSON.parse(readFileSync(join(DIR, "desktop.schema.json"), "utf8"));
type S = Record<string, unknown>;
/** A small structural check against the contract's own schema ($ref, type, required, properties,
 *  items, enum, oneOf/anyOf) — the terminal declares no JSON-schema validator; the contract's
 *  validate.mjs holds the goldens to the full schema. */
function check(s: S | undefined, v: unknown, at: string): string {
  if (!s) return "";
  if (typeof s.$ref === "string") return check(schema.$defs[(s.$ref as string).split("/").pop()!], v, at);
  for (const k of ["oneOf", "anyOf"] as const) {
    const alts = s[k] as S[] | undefined;
    if (alts && !alts.some((alt) => check(alt, v, at) === "")) return `${at}: matches no ${k} branch`;
  }
  if (Array.isArray(s.enum) && !(s.enum as unknown[]).includes(v)) return `${at}: not one of ${JSON.stringify(s.enum)}`;
  const types = s.type === undefined ? [] : ([] as unknown[]).concat(s.type);
  const kind = v === null ? "null" : Array.isArray(v) ? "array" : Number.isInteger(v) ? "integer" : typeof v;
  if (types.length && !types.some((t) => t === kind || (t === "number" && kind === "integer"))) return `${at}: ${kind} is not ${types.join("|")}`;
  if (kind === "object") {
    const o = v as S;
    for (const r of (s.required as string[] | undefined) ?? []) if (!(r in o)) return `${at}: missing ${r}`;
    for (const [k, sub] of Object.entries((s.properties as Record<string, S> | undefined) ?? {})) {
      if (k in o) { const e = check(sub, o[k], `${at}.${k}`); if (e) return e; }
    }
  }
  if (kind === "array" && s.items) {
    for (const [i, x] of (v as unknown[]).entries()) { const e = check(s.items as S, x, `${at}[${i}]`); if (e) return e; }
  }
  return "";
}
const validate = (shape: string, data: unknown) => check(schema.$defs[shape], data, shape);

const card: CardView = JSON.parse(readFileSync(join(DIR, "golden", "CardView.member.json"), "utf8"));
const sections: DesktopSections = { presence_visibility: "off", hidden_workspaces: 0, workspaces: [], agents: [], files: [] };
const desk = (over: Partial<Desk> = {}): Desk => ({ version: 2, undo_version: null, saved: [], proposals: [], placements: [], layout: {}, ...over });

describe("desktop.v1 Desktop", () => {
  it("the composition without cards (desktop_cards off) conforms", () => {
    expect(validate("Desktop", composeDesktop(desk(), sections))).toBe("");
  });

  it("the composition with cards, card proposals, invites and an active filter conforms", () => {
    const proposal = JSON.parse(readFileSync(join(DIR, "golden", "CardProposal.create.json"), "utf8"));
    const d = composeDesktop(desk({
      cards: [card], card_proposals: [proposal], card_invites: [{ ...card, id: "r9", ref: "card:r9" }],
      layout: { filters: { cards: { where: "state == \"Doing\"" } } },
    }), sections);
    expect(d.sections.find((s) => s.id === "cards")!.items).toHaveLength(1);
    expect(validate("Desktop", d)).toBe("");
  });
  it("crm-api's own desk read with cards (crm.v1 golden) composes into a conforming Desktop", () => {
    const crmDesk = JSON.parse(readFileSync(join(DIR, "../../../crm/contracts/crm.v1/golden/Desk.with-cards.json"), "utf8"));
    const d = composeDesktop(crmDesk, sections);
    expect(d.sections.find((s) => s.id === "cards")!.items.length).toBe(crmDesk.cards.length);
    expect(validate("Desktop", d)).toBe("");
  });
});
