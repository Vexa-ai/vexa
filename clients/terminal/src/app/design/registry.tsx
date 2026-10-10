"use client";
/** THE CATALOGUE'S REGISTRY (guidelines §9): one entry per ui-kit component, each with the
 *  components it shows and a demo built from fixtures. Gate G13 (`designCatalogue.test.ts`) fails
 *  when a component exported from `src/ui-kit/index.tsx` has no entry here, and renders every entry
 *  in both themes. Adding a primitive means adding it here in the same PR. */
import { useState, type ReactNode } from "react";
import { Bell, FileText, Pin, Plus, Search, X } from "lucide-react";
import {
  Badge, Breadcrumb, Button, Card, Checkbox, Chip, ChipRow, Code, ConfirmDialog, DateText, Dialog, Drawer, EmptyState, EntityChip,
  ErrorState, ExternalLink, Fold, Icon, IconButton, Input, Kbd, KeyValue, ListRow, Menu, OverflowStrip, PanelHeader,
  Popover, SecretReveal, Select, Sheet, Skeleton, SourceList, Spinner, Splitter, StatusDot, Tabs, Tag, Textarea, Toaster,
  Tooltip, Truncate, kvValue, toast,
} from "../../ui-kit";
import { EMAIL, PATH_LONG, PROPERTIES, SOURCES, TABS, TOKEN, URL_LONG, WORKSPACE, PERSON, COMPANY } from "./fixtures";

export type Entry = {
  id: string; title: string; components: string[]; note: string;
  /** Render at pane widths (320 / 480 / 720) to show container-query behaviour. */
  widths?: number[];
  demo: () => ReactNode;
};

const Row = ({ children }: { children: ReactNode }) => <div className="vx-cat-row">{children}</div>;

function SplitterDemo() {
  const [w, setW] = useState(240);
  return <div className="vx-cat-splitbox"><div className="vx-cat-splitpane" style={{ width: w }}>Pane · {w}px</div>
    <Splitter label="Resize the example pane" value={w} bounds={{ min: 160, max: 360, def: 240 }} grows="right"
      onPreview={setW} onCommit={(v) => setW(v ?? 240)} style={{ left: w - 4 }} /></div>;
}
function SheetDemo() {
  const [open, setOpen] = useState(false);
  return <div className="vx-cat-overlaybox"><Button onClick={() => setOpen(true)}>Open the sheet</Button>
    <Sheet form="overlay" open={open} onClose={() => setOpen(false)} width={260} label="Example sheet">
      <PanelHeader title="Example sheet" trailing={<Button variant="ghost" onClick={() => setOpen(false)}>Close</Button>} />
      <div className="vx-cat-pad">Esc, the scrim or Close dismisses it; focus returns to the button.</div>
    </Sheet></div>;
}
function DrawerDemo() {
  const [open, setOpen] = useState(false);
  return <div className="vx-cat-overlaybox"><Button onClick={() => setOpen(true)}>Open the drawer</Button>
    <Drawer open={open} onClose={() => setOpen(false)} width={220} label="Example drawer">
      <div className="vx-cat-pad">{["Weekly sync", "Pricing questions", "Onboarding plan"].map((c) => <ListRow key={c} title={c} />)}</div>
    </Drawer></div>;
}
function TabsDemo() {
  const [v, setV] = useState("active");
  return <Tabs label="Example sections" value={v} onChange={setV} items={[{ key: "active", label: "Active" }, { key: "all", label: "All" }, { key: "archived", label: "Archived" }]} />;
}
function SelectDemo() {
  const [v, setV] = useState("dark");
  return <Select label="Theme" value={v} onChange={setV} options={[{ value: "dark", label: "Dark" }, { value: "light", label: "Light" }]} />;
}
function DialogDemo() {
  const [open, setOpen] = useState<"" | "dialog" | "confirm">("");
  return <Row>
    <Button onClick={() => setOpen("dialog")}>Open a dialog</Button>
    <Button variant="danger-ghost" onClick={() => setOpen("confirm")}>Delete workspace…</Button>
    <Dialog open={open === "dialog"} onClose={() => setOpen("")} title="Rename the chat"
      footer={<><Button variant="ghost" onClick={() => setOpen("")}>Cancel</Button><Button variant="primary" onClick={() => setOpen("")}>Rename chat</Button></>}>
      <Input label="Chat name" defaultValue="Weekly sync" />
    </Dialog>
    <ConfirmDialog open={open === "confirm"} onCancel={() => setOpen("")} onConfirm={() => setOpen("")}
      title="Delete this workspace?" consequence={`Every page in ${WORKSPACE} is deleted for everyone it is shared with. This cannot be undone.`}
      confirmLabel="Delete workspace" typeToConfirm={WORKSPACE} />
  </Row>;
}
function PopoverDemo() {
  const [open, setOpen] = useState(false);
  return <span className="vx-menu-anchor"><Button onClick={() => setOpen((v) => !v)}>Show details</Button>
    <Popover open={open} onClose={() => setOpen(false)} label="Details">A popover holds a little more than a tooltip and closes on Esc or an outside click.</Popover></span>;
}
function CheckboxDemo() {
  const [on, setOn] = useState(true);
  return <Row><Checkbox checked={on} onChange={() => setOn((v) => !v)} label="Example option" /> Example option</Row>;
}

export const REGISTRY: Entry[] = [
  { id: "button", title: "Button", components: ["Button"], note: "primary is the view's one action; danger only confirms a destructive dialog.",
    demo: () => <><Row>{(["primary", "secondary", "ghost", "danger", "danger-ghost"] as const).map((v) => <Button key={v} variant={v}>{v === "primary" ? "Send" : v === "danger" ? "Delete chat" : v === "danger-ghost" ? "Delete…" : v === "ghost" ? "Cancel" : "Connect calendar"}</Button>)}</Row>
      <Row><Button size="md" icon={<Plus size={16} strokeWidth={1.75} />}>New chat</Button><Button loading>Saving</Button><Button disabled>Disabled</Button></Row></> },
  { id: "icon-button", title: "IconButton", components: ["IconButton"], note: "A name is required; it is the tooltip. pressed toggles.",
    demo: () => <Row>{(["xs", "sm", "md"] as const).map((s) => <IconButton key={s} label={`Search (${s})`} size={s}><Search size={16} strokeWidth={1.75} /></IconButton>)}
      <IconButton label="Pin this tab" pressed><Pin size={16} strokeWidth={1.75} /></IconButton><IconButton label="Notifications" variant="secondary"><Bell size={16} strokeWidth={1.75} /></IconButton></Row> },
  { id: "icon", title: "Icon (legacy set)", components: ["Icon"], note: "The original SVG icon set; new code uses lucide.",
    demo: () => <Row>{["file", "cal", "mic", "send", "folder", "pin", "copy"].map((n) => <Icon key={n} name={n} size={16} />)}</Row> },
  { id: "checkbox", title: "Checkbox", components: ["Checkbox"], note: "A real role=checkbox; Space and Enter toggle.", demo: () => <CheckboxDemo /> },
  { id: "status", title: "Spinner, StatusDot, Kbd", components: ["Spinner", "StatusDot", "Kbd"], note: "A dot always has its word; a spinner always sits beside a label.",
    demo: () => <Row><Spinner /> <StatusDot tone="success" label="Live" /><StatusDot tone="warning" label="Joining" /><StatusDot label="Held" /><Kbd>⌘K</Kbd></Row> },
  { id: "badge-chip", title: "Badge, Tag, Chip, EntityChip", components: ["Badge", "Tag", "Chip", "EntityChip"], note: "Static is a badge or tag (radius 4); interactive is a chip (radius 6, a border).",
    demo: () => <><Row><Badge tone="success" dot>Live</Badge><Badge>Held</Badge><Badge tone="warning">Waiting</Badge><Badge tone="danger">2 errors</Badge><Badge tone="info">3 new</Badge><Tag>Company</Tag></Row>
      <Row><Chip selected icon={<FileText size={14} strokeWidth={1.75} />}>Writes to personal</Chip><Chip onRemove={() => {}} removeLabel={`Remove ${WORKSPACE}`}>{WORKSPACE}</Chip><Chip ghost icon={<Plus size={14} strokeWidth={1.75} />}>Add workspace</Chip></Row>
      <Row><EntityChip kind="person" onOpen={() => {}}>{PERSON}</EntityChip><EntityChip kind="company" onOpen={() => {}}>{COMPANY}</EntityChip><EntityChip kind="meeting" onOpen={() => {}}>Weekly sync</EntityChip><EntityChip kind="doc" onOpen={() => {}}>Rollout plan</EntityChip></Row></> },
  { id: "chip-row", title: "ChipRow", components: ["ChipRow"], note: "One line of chips; \"+N\" says how many are out of view and opens the row.", widths: [320, 480],
    demo: () => <ChipRow label="context chips">{["Schedule · today", "In meeting · Weekly sync", "Workspace · Example workspace", "Today"].map((c) => <Chip key={c} onRemove={() => {}} removeLabel={`Remove ${c}`}>{c}</Chip>)}</ChipRow> },
  { id: "fields", title: "Input, Textarea", components: ["Input", "Textarea"], note: "Label above; the error is linked by aria-describedby.",
    demo: () => <div className="vx-cat-col"><Input label="Workspace name" placeholder="e.g. Example workspace" /><Input label="Email" defaultValue="not-an-email" error="Enter an email address, like name@example.com" /><Textarea label="Notes" hint="Markdown is fine." /></div> },
  { id: "menu", title: "Menu, Select, Popover", components: ["Menu", "Select", "Popover"], note: "Arrows, Home/End, typeahead; Esc returns focus to the trigger.",
    demo: () => <Row><Menu label="More actions" variant="chip" items={[{ key: "r", label: "Rename", onSelect: () => {} }, { key: "p", label: "Pin", onSelect: () => {} }, { key: "d", label: "Delete chat", danger: true, separatorBefore: true, onSelect: () => {} }]} /><SelectDemo /><PopoverDemo /></Row> },
  { id: "tabs", title: "Tabs, OverflowStrip", components: ["Tabs", "OverflowStrip"], note: "Roving tabindex. A strip that hides a tab fades and lists every tab in its overflow menu.", widths: [320, 480],
    demo: () => <div className="vx-cat-col"><TabsDemo /><div className="vx-cat-strip"><OverflowStrip label="tabs" activeKey={TABS[0]} items={TABS.map((t) => ({ key: t, label: t, onSelect: () => {} }))}>
      {TABS.map((t) => <span key={t} data-strip-key={t} className="vx-tag vx-cat-tab">{t}</span>)}</OverflowStrip></div></div> },
  { id: "breadcrumb", title: "Breadcrumb", components: ["Breadcrumb"], note: "Names, not slugs; the middle collapses past four.",
    demo: () => <div className="vx-cat-col"><Breadcrumb items={[{ key: "a", label: "personal", onSelect: () => {} }, { key: "b", label: "meetings", onSelect: () => {} }, { key: "c", label: "Weekly sync" }]} />
      <Breadcrumb items={["personal", "kg", "entities", "companies", "notes", "Example Company"].map((l, i, a) => ({ key: l, label: l, onSelect: i < a.length - 1 ? () => {} : undefined }))} /></div> },
  { id: "rows", title: "ListRow, PanelHeader, Card", components: ["ListRow", "PanelHeader", "Card"], note: "Selected is a fill, weight and a bar — never colour alone.",
    demo: () => <div className="vx-cat-col"><PanelHeader title="Chats" actions={<IconButton label="New chat"><Plus size={16} strokeWidth={1.75} /></IconButton>} inset="rail" />
      <ListRow title="Weekly sync" meta="6:29 PM" selected leading={<span className="vx-dot" data-tone="success" />} /><ListRow title="Pricing questions" meta="Yesterday" actions={<IconButton label="Delete chat" size="xs"><X size={14} strokeWidth={1.75} /></IconButton>} />
      <Card title="Workspace" actions={<Button variant="ghost">Open</Button>}>Shared with 3 people. Last changed Thursday.</Card><Card onOpen={() => {}} raised>An interactive card is one button.</Card></div> },
  { id: "dialog", title: "Dialog, ConfirmDialog", components: ["Dialog", "ConfirmDialog"], note: "Replaces window.confirm. The confirm names the act.", demo: () => <DialogDemo /> },
  { id: "states", title: "EmptyState, Skeleton, ErrorState", components: ["EmptyState", "Skeleton", "ErrorState"], note: "Loading, empty, error and content never look alike (P18).",
    demo: () => <div className="vx-cat-col"><Skeleton lines={3} delay={0} /><EmptyState icon={<FileText size={24} strokeWidth={1.75} />} action={<Button variant="primary">New page</Button>}>No pages here yet. They appear when the conversation writes one.</EmptyState>
      <ErrorState what="Could not load this workspace" why="The workspace service did not answer." fix={<Button>Retry</Button>} detail="workspace-api: HTTP 503 (unavailable)" /></div> },
  { id: "tooltip", title: "Tooltip, Toaster", components: ["Tooltip", "Toaster"], note: "Tooltips are supplementary; toasts only for completed background outcomes.",
    demo: () => <Row><Tooltip text="Copies the page's markdown"><Button>Hover or focus me</Button></Tooltip><Button onClick={() => toast("Workspace shared")}>Show a toast</Button><Toaster /></Row> },
  { id: "keyvalue", title: "KeyValue", components: ["KeyValue"], note: "Two columns from 360px of pane width; stacked below. Never breaks a word.", widths: [320, 480, 720],
    demo: () => <KeyValue items={Object.entries(PROPERTIES).map(([key, value]) => ({ key, value: kvValue(value) }))} /> },
  { id: "truncate", title: "Truncate, DateText", components: ["Truncate", "DateText"], note: "One line, an ellipsis, the full value on hover. Dates never split.", widths: [320],
    demo: () => <div className="vx-cat-col"><Truncate text={EMAIL} href={`mailto:${EMAIL}`} /><Truncate text={URL_LONG} href={URL_LONG} /><Truncate text={PATH_LONG} mode="middle" />
      <Row><DateText value="2026-10-09" /><DateText value={new Date(Date.now() - 3600_000)} /><DateText value="2025-03-14T10:00:00Z" /></Row></div> },
  { id: "fold", title: "Fold", components: ["Fold"], note: "One control, two forms, by pane width (here at 400).", widths: [320, 480],
    demo: () => <Fold at={400} wide={<Row><Button>Attach files</Button><Button>Dictate</Button></Row>}
      narrow={<Menu label="More composer actions" items={[{ key: "a", label: "Attach files", onSelect: () => {} }, { key: "d", label: "Dictate", onSelect: () => {} }]} />} /> },
  { id: "links", title: "ExternalLink, Code, SecretReveal", components: ["ExternalLink", "Code", "SecretReveal"], note: "http(s) only; secrets masked and never in an attribute.",
    demo: () => <div className="vx-cat-col"><Row><ExternalLink href="https://example.com/docs">Example docs</ExternalLink><ExternalLink href="data:text/plain,example">A refused link renders as text</ExternalLink></Row>
      <Row><Code>a1b2c3d4e5f6a7b8c9d0</Code></Row><Code block>{"curl -H \"X-API-Key: $KEY\" https://api.example.com/meetings"}</Code><SecretReveal value={TOKEN} label="example token" /></div> },
  { id: "sources", title: "SourceList", components: ["SourceList"], note: "A date shown once; the first three, then Show more.", demo: () => <SourceList sources={SOURCES} /> },
  { id: "splitter", title: "Splitter", components: ["Splitter"], note: "Drag, or focus and use ←/→, Shift, Home/End, Enter.", demo: () => <SplitterDemo /> },
  { id: "sheet", title: "Sheet, Drawer", components: ["Sheet", "Drawer"], note: "The overlay forms of the side panes: modal, focus-trapped, Esc closes.", demo: () => <Row><SheetDemo /><DrawerDemo /></Row> },
];
