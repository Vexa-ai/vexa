"use client";
import { useState, type CSSProperties } from "react";
import { Hash, Pause, Play, Plus, RotateCcw, SquareStack, StepForward, UserPlus } from "lucide-react";
import { useMeetingSource, type MeetingSourceMode, type MockInjectKind, type MockScenarioId } from "./useMeeting";

const strip: CSSProperties = {
  flex: "none",
  borderBottom: "1px solid var(--line)",
  background: "var(--sidebar)",
  color: "var(--t2)",
  fontSize: 12,
  minWidth: 0,
};

const row: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
  flexWrap: "wrap",
  minWidth: 0,
};

const label: CSSProperties = {
  color: "var(--t3)",
  fontSize: 11,
  fontWeight: 700,
  textTransform: "uppercase",
  letterSpacing: 0,
  whiteSpace: "nowrap",
};

function controlButton(active = false): CSSProperties {
  return {
    height: 28,
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    gap: 6,
    border: `1px solid ${active ? "var(--accent)" : "var(--line2)"}`,
    borderRadius: 7,
    background: active ? "var(--accentbg)" : "var(--panel)",
    color: active ? "var(--accent)" : "var(--t1)",
    padding: "0 9px",
    fontSize: 12,
    fontWeight: 650,
    cursor: "pointer",
    whiteSpace: "nowrap",
  };
}

const selectStyle: CSSProperties = {
  height: 28,
  border: "1px solid var(--line2)",
  borderRadius: 7,
  background: "var(--panel)",
  color: "var(--t1)",
  padding: "0 8px",
  fontSize: 12,
  outline: "none",
};

function ModeButton({ mode, active, onClick }: { mode: MeetingSourceMode; active: boolean; onClick: () => void }) {
  return (
    <button type="button" aria-pressed={active} onClick={onClick} style={controlButton(active)}>
      {mode === "live" ? "Live" : "Mock"}
    </button>
  );
}

function InjectButton({ kind, onClick }: { kind: MockInjectKind; onClick: () => void }) {
  const icon = kind === "speaker"
    ? <UserPlus size={14} />
    : kind === "number"
      ? <Hash size={14} />
      : kind === "card"
        ? <SquareStack size={14} />
        : <Plus size={14} />;
  const text = kind[0].toUpperCase() + kind.slice(1);
  return (
    <button type="button" title={`Inject ${kind}`} onClick={onClick} style={controlButton(false)}>
      {icon}
      <span>{text}</span>
    </button>
  );
}

export function EvalPanel() {
  const source = useMeetingSource();
  const [open, setOpen] = useState(false);
  if (!source) return null;

  const fallback = source.mode === "live" && !source.liveHasData;
  const modeLabel = fallback ? "Mock fallback" : source.activeMode === "mock" ? "Mock" : "Live";
  const playbackPct = source.mock.metrics.playbackPct ?? 0;

  return (
    <section style={strip} aria-label="Mock and eval bench">
      <div style={{ ...row, minHeight: 34, padding: "0 10px" }}>
        <button type="button" onClick={() => setOpen((next) => !next)} aria-expanded={open} style={controlButton(open)}>
          <SquareStack size={14} />
          <span>Mock/Eval</span>
        </button>
        <span style={{ color: fallback ? "var(--accent)" : "var(--t3)", fontFamily: "var(--mono)", fontSize: 11 }}>{modeLabel}</span>
        <span style={{ color: "var(--t3)", fontFamily: "var(--mono)", fontSize: 11 }}>{playbackPct}%</span>
      </div>
      {open && (
        <div style={{ display: "flex", flexDirection: "column", gap: 9, padding: "0 10px 10px", borderTop: "1px solid var(--line)", background: "var(--bg)" }}>
          <div style={{ ...row, paddingTop: 10 }}>
            <span style={label}>Source</span>
            <ModeButton mode="live" active={source.mode === "live"} onClick={() => source.setMode("live")} />
            <ModeButton mode="mock" active={source.mode === "mock"} onClick={() => source.setMode("mock")} />
            <span style={label}>Scenario</span>
            <select
              value={source.mockState.scenarioId}
              onChange={(event) => source.controls.setScenario(event.currentTarget.value as MockScenarioId)}
              style={{ ...selectStyle, minWidth: 136 }}
            >
              {source.scenarios.map((scenario) => <option key={scenario.id} value={scenario.id}>{scenario.label}</option>)}
            </select>
          </div>
          <div style={row}>
            <span style={label}>Playback</span>
            <button type="button" onClick={source.mockState.playing ? source.controls.pause : source.controls.play} style={controlButton(source.mockState.playing)}>
              {source.mockState.playing ? <Pause size={14} /> : <Play size={14} />}
              <span>{source.mockState.playing ? "Pause" : "Play"}</span>
            </button>
            <button type="button" onClick={source.controls.step} style={controlButton(false)}>
              <StepForward size={14} />
              <span>Step</span>
            </button>
            <button type="button" onClick={source.controls.reset} style={controlButton(false)}>
              <RotateCcw size={14} />
              <span>Reset</span>
            </button>
            <span style={label}>Speed</span>
            <select
              value={String(source.mockState.speed)}
              onChange={(event) => source.controls.setSpeed(Number(event.currentTarget.value))}
              style={{ ...selectStyle, width: 74 }}
            >
              <option value="0.5">0.5x</option>
              <option value="1">1x</option>
              <option value="1.5">1.5x</option>
              <option value="2">2x</option>
              <option value="4">4x</option>
            </select>
          </div>
          <div style={row}>
            <span style={label}>Inject</span>
            {(["entity", "number", "card", "speaker"] as MockInjectKind[]).map((kind) => (
              <InjectButton key={kind} kind={kind} onClick={() => source.controls.inject(kind)} />
            ))}
          </div>
        </div>
      )}
    </section>
  );
}
