"use client";
/** /mdx-demo — standalone showcase of the MdxDoc workspace renderer (no gateway needed).
 *  Left: the raw agent-authored markdown source. Right: the live rendered interface.
 *  The second sample is intentionally malformed to demonstrate the plain-markdown fallback. */
import { MdxDoc } from "../../ui-kit/MdxDoc";

const SAMPLE = `# Jane Liu

VP of Engineering at [[Company ACME]] — primary contact for the platform migration.

<Note>
  Last synced from the **2026-06-30 sync call**. The agent updates this doc after every meeting.
</Note>

<CardGroup cols={2}>
  <Card title="Open tasks" icon="tasks" href="kg/entities/project/platform-migration.md">
    3 items blocked on ACME's security review.
  </Card>
  <Card title="Meeting notes" icon="cal" href="kg/entities/meeting/2026-06-30-sync.md">
    Weekly sync — decisions on rollout phasing.
  </Card>
</CardGroup>

Links: [migration plan](kg/entities/project/platform-migration.md) opens as a workspace
tab; [ACME docs](https://example.com/acme) opens in the browser.

## Rollout plan

<Steps>
  <Step title="Security review sign-off">
    ACME infosec reviews the data-residency addendum. Owner: [[Jane Liu]].
  </Step>
  <Step title="Pilot cohort">
    20 seats in the trading desk, two weeks, success = daily active use.
  </Step>
  <Step title="Org-wide rollout">
    Gated on pilot metrics and the *quarterly budget checkpoint*.
  </Step>
</Steps>

## Details

<Tabs>
  <Tab title="Background">
    Jane leads a 40-person platform org. Prefers async updates; escalate only via email.

    - **Skills:** Rust, distributed systems
    - **Location:** San Francisco
  </Tab>
  <Tab title="History">
    | Date | Event |
    | --- | --- |
    | 2026-05-12 | Intro call |
    | 2026-06-30 | Migration sync — agreed pilot scope |
  </Tab>
</Tabs>

<Warning>
  Contract renewal is **45 days out** — surface pricing before the pilot ends.
</Warning>
`;

const BROKEN = `# Fallback demo

This file has invalid MDX (an unclosed <tag and stray {braces}), the kind of thing
an agent occasionally writes. It still renders — as plain markdown.

- the page never breaks
- worst case we lose interactivity, not content
`;

function Pane({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ flex: 1, minWidth: 0, border: "1px solid var(--line)", borderRadius: 12, background: "var(--bg)", overflow: "hidden", display: "flex", flexDirection: "column" }}>
      <div style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--t3)", padding: "8px 13px", borderBottom: "1px solid var(--line)", background: "var(--panel)" }}>{title}</div>
      <div style={{ padding: "16px 18px", overflow: "auto", flex: 1 }}>{children}</div>
    </div>
  );
}

export default function MdxDemo() {
  return (
    <div style={{ minHeight: "100vh", background: "var(--bg)", color: "var(--t1)", padding: "26px 28px", display: "flex", flexDirection: "column", gap: 18 }}>
      <div>
        <div style={{ fontSize: 17, fontWeight: 600 }}>Workspace MDX renderer</div>
        <div style={{ fontSize: 12.5, color: "var(--t3)", marginTop: 3 }}>
          agent-authored markdown (left) → live interface (right) · runtime-compiled with @mdx-js/mdx, closed component registry, plain-markdown fallback
        </div>
      </div>
      <div style={{ display: "flex", gap: 14, alignItems: "stretch", minHeight: 0 }}>
        <Pane title="kg/entities/person/jane-liu.md (source)">
          <pre style={{ fontFamily: "var(--mono)", fontSize: 11.5, lineHeight: 1.55, color: "var(--t2)", whiteSpace: "pre-wrap" }}>{SAMPLE}</pre>
        </Pane>
        <Pane title="rendered">
          <div style={{ fontSize: 14, lineHeight: 1.6 }}><MdxDoc>{SAMPLE}</MdxDoc></div>
        </Pane>
      </div>
      <div style={{ display: "flex", gap: 14, alignItems: "stretch", minHeight: 0 }}>
        <Pane title="malformed MDX (source)">
          <pre style={{ fontFamily: "var(--mono)", fontSize: 11.5, lineHeight: 1.55, color: "var(--t2)", whiteSpace: "pre-wrap" }}>{BROKEN}</pre>
        </Pane>
        <Pane title="rendered — graceful fallback">
          <div style={{ fontSize: 14, lineHeight: 1.6 }}><MdxDoc>{BROKEN}</MdxDoc></div>
        </Pane>
      </div>
    </div>
  );
}
