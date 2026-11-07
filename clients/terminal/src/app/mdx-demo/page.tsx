"use client";
/** /mdx-demo — standalone showcase of the MdxDoc workspace renderer (no gateway needed).
 *  Left: the raw agent-authored markdown source. Right: the live rendered interface.
 *  The second sample is intentionally malformed to demonstrate the plain-markdown fallback.
 *
 *  Every name below is a PLACEHOLDER on purpose ("Person Name", "Company Name" — generic nouns,
 *  nobody's identity). This route renders a doc, and a rendered doc naming a plausible person at a
 *  plausible company is indistinguishable from a real record: screenshot it, quote it, or let an
 *  agent read it, and the fake becomes knowledge. What is being demonstrated is the RENDERER, and
 *  a placeholder demonstrates it exactly as well. (Angle brackets are avoided deliberately —
 *  MdxDoc escapes an unknown `<Tag>`, so `<Full Name>` would render with a stray backslash.) */
import { MdxDoc } from "../../ui-kit/MdxDoc";
import "./mdx-demo.css";

const SAMPLE = `# Person Name

VP of Engineering at [[Company Name]] — primary contact for the platform migration.

<Note>
  Last synced from the **2026-06-30 sync call**. The agent updates this doc after every meeting.
</Note>

<CardGroup cols={2}>
  <Card title="Open tasks" icon="tasks" href="kg/entities/project/platform-migration.md">
    3 items blocked on Company Name's security review.
  </Card>
  <Card title="Meeting notes" icon="cal" href="kg/entities/meeting/2026-06-30-sync.md">
    Weekly sync — decisions on rollout phasing.
  </Card>
</CardGroup>

Links: [migration plan](kg/entities/project/platform-migration.md) opens as a workspace
tab; [Company Name docs](https://example.com/docs) opens in the browser.

## Rollout plan

<Steps>
  <Step title="Security review sign-off">
    Company Name infosec reviews the data-residency addendum. Owner: [[Person Name]].
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
    Person Name leads a 40-person platform org. Prefers async updates; escalate only via email.

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

\`\`\`mermaid
flowchart LR
  A[Pilot scope] --> B{Security review}
  B -->|passed| C[Rollout]
  B -->|blocked| D[Escalate]
\`\`\`

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
    <div className="vx-demo-pane">
      <div className="vx-demo-pane-title">{title}</div>
      <div className="vx-demo-pane-body">{children}</div>
    </div>
  );
}

export default function MdxDemo() {
  return (
    <div className="vx-demo">
      <div>
        <div className="vx-demo-title">Workspace MDX renderer</div>
        <div className="vx-demo-sub">
          agent-authored markdown (left) → live interface (right) · parsed and drawn as elements (no code compiled), closed component registry, plain-markdown fallback
        </div>
      </div>
      <div className="vx-demo-row">
        <Pane title="kg/entities/person/person-slug.md (source)">
          <pre className="vx-demo-source">{SAMPLE}</pre>
        </Pane>
        <Pane title="rendered">
          <div className="vx-demo-doc"><MdxDoc>{SAMPLE}</MdxDoc></div>
        </Pane>
      </div>
      <div className="vx-demo-row">
        <Pane title="malformed MDX (source)">
          <pre className="vx-demo-source">{BROKEN}</pre>
        </Pane>
        <Pane title="rendered — graceful fallback">
          <div className="vx-demo-doc"><MdxDoc>{BROKEN}</MdxDoc></div>
        </Pane>
      </div>
    </div>
  );
}
