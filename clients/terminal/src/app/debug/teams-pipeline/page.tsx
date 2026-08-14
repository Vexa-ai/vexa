"use client";

import { useEffect, useRef, useState } from "react";
import { LiveTranscriptEngine, type EngineSegment } from "../../../canvas/LiveTranscriptEngine";

type PipelineRow = {
  csrc: number;
  speaker: string;
  segmentId: string;
  text: string;
  startMs: number;
  endMs: number;
  completed: boolean;
};
const API = "/api/debug/teams-pipeline";
const AUDIO = `${API}/audio`;
const clock = (seconds: number): string => `${Math.floor(seconds / 60).toString().padStart(2, "0")}:${Math.floor(seconds % 60).toString().padStart(2, "0")}`;
const latency = (milliseconds: number | null): string => milliseconds == null ? "—" : `+${(milliseconds / 1000).toFixed(1)}s`;

export default function TeamsPipelineWitness(): React.ReactElement {
  const [segments, setSegments] = useState<EngineSegment[]>([]);
  const [status, setStatus] = useState("Ready");
  const [callPosition, setCallPosition] = useState(60);
  const [transcriptEdgeMs, setTranscriptEdgeMs] = useState<number | null>(null);
  const [lastInferenceAt, setLastInferenceAt] = useState<number | null>(null);
  const [lastSchedulerAt, setLastSchedulerAt] = useState<number | null>(null);
  const [schedulerGapMs, setSchedulerGapMs] = useState<number | null>(null);
  const [schedulerRange, setSchedulerRange] = useState<{ min: number; max: number; samples: number } | null>(null);
  const [knownLaneCount, setKnownLaneCount] = useState(0);
  const [lastWhisperStartAt, setLastWhisperStartAt] = useState<number | null>(null);
  const [lastWhisperCallGapMs, setLastWhisperCallGapMs] = useState<number | null>(null);
  const [whisperCallRange, setWhisperCallRange] = useState<{ min: number; max: number; samples: number } | null>(null);
  const [ordinaryWhisperStarts, setOrdinaryWhisperStarts] = useState(0);
  const [forcedWhisperStarts, setForcedWhisperStarts] = useState(0);
  const [bufferWhisperStarts, setBufferWhisperStarts] = useState(0);
  const [confirmedCount, setConfirmedCount] = useState(0);
  const [pendingCount, setPendingCount] = useState(0);
  const [contestedCount, setContestedCount] = useState(0);
  const [identity, setIdentity] = useState("Awaiting corroboration");
  const [error, setError] = useState<string | null>(null);
  const [audioState, setAudioState] = useState("Audio ready");
  const audioRef = useRef<HTMLAudioElement>(null);
  const raw = useRef(new Map<string, PipelineRow>());
  const confirmed = useRef(new Set<string>());
  const startedAt = useRef<number | null>(null);
  const cutStartAt = useRef<number | null>(null);
  const previousSchedulerAt = useRef<number | null>(null);
  const schedulerRangeRef = useRef<{ min: number; max: number; samples: number } | null>(null);
  const whisperCallRangeRef = useRef<{ min: number; max: number; samples: number } | null>(null);

  const publish = (): void => {
    const rows = [...raw.current.values()].sort((left, right) => left.startMs - right.startMs || left.csrc - right.csrc);
    setSegments(rows.map((row) => ({
      speaker: row.speaker,
      // Contest ownership is a pipeline/API concern. The ordinary renderer understands the
      // wire marker, but this page must not infer or rewrite speaker ownership independently.
      text: row.text,
      tsMs: row.startMs,
      id: row.segmentId,
      completed: row.completed,
    })));
    setConfirmedCount(rows.filter((row) => row.completed).length);
    setPendingCount(rows.filter((row) => !row.completed).length);
    setTranscriptEdgeMs(rows.length ? Math.max(...rows.map((row) => row.endMs)) : null);
    setContestedCount(rows.filter((row) => /⟦[^⟧]+⟧\{[^}]+\}/.test(row.text)).length);
    const names = [...new Set(rows.map((row) => row.speaker).filter((name) => name && !/^Speaker [A-Z]+$/.test(name)))];
    setIdentity(names.length ? `Named live: ${names.join(", ")}` : "Awaiting corroboration — Speaker A/B are explicit unknowns");
  };

  useEffect(() => {
    let closed = false;
    let stream: EventSource | null = null;
    let animation = 0;
    void fetch(`${API}/reference`, { cache: "no-store" })
      .then((response) => {
        if (!response.ok) throw new Error(`reference HTTP ${response.status}`);
        return response.json();
      })
      .then(() => {
        if (closed) return;
        stream = new EventSource(`${API}/events`);
        stream.onmessage = (message) => {
          const payload = JSON.parse(message.data);
          if (payload.type === "ready") {
            cutStartAt.current = Number(payload.cutStartMs);
            return;
          }
          if (payload.type === "snapshot") {
            raw.current.clear();
            confirmed.current.clear();
            for (const row of (payload.segments ?? []) as PipelineRow[]) {
              if (!row.completed && !row.text.trim()) continue;
              raw.current.set(row.segmentId, row);
              if (row.completed && row.text.trim()) confirmed.current.add(row.segmentId);
            }
            startedAt.current = payload.startedAtWallMs == null ? null : Number(payload.startedAtWallMs);
            const cadence = payload.cadence ?? {};
            const gapSamples = Number(cadence.ordinaryGapSamples ?? 0);
            const wallMin = Number(cadence.ordinaryWallGapMinMs);
            const wallMax = Number(cadence.ordinaryWallGapMaxMs);
            const wallRange = gapSamples > 0 && Number.isFinite(wallMin) && Number.isFinite(wallMax)
              ? { min: wallMin, max: wallMax, samples: gapSamples }
              : null;
            whisperCallRangeRef.current = wallRange;
            setWhisperCallRange(wallRange);
            setOrdinaryWhisperStarts(Number(cadence.ordinaryStarts ?? 0));
            setForcedWhisperStarts(Number(cadence.forcedStarts ?? 0));
            setBufferWhisperStarts(Number(cadence.bufferStarts ?? 0));
            if (payload.state === "running" && startedAt.current != null) {
              const position = Math.max(0, (Date.now() - startedAt.current) / 1000);
              if (audioRef.current) {
                audioRef.current.currentTime = Math.min(position, audioRef.current.duration || position);
                void audioRef.current.play();
              }
              setStatus("LIVE PIPELINE");
            } else if (payload.state === "complete") setStatus("Complete");
            else if (payload.state === "failed") setStatus("Failed");
            publish();
            return;
          }
          if (payload.type === "started") {
            startedAt.current = Number(payload.startedAtWallMs);
            const delay = Math.max(0, startedAt.current - Date.now());
            window.setTimeout(() => { void audioRef.current?.play(); setStatus("LIVE PIPELINE"); }, delay);
            return;
          }
          if (payload.type === "scheduler") {
            const now = Date.now();
            if (previousSchedulerAt.current != null) {
              const gap = now - previousSchedulerAt.current;
              setSchedulerGapMs(gap);
              const prior = schedulerRangeRef.current;
              const next = prior
                ? { min: Math.min(prior.min, gap), max: Math.max(prior.max, gap), samples: prior.samples + 1 }
                : { min: gap, max: gap, samples: 1 };
              schedulerRangeRef.current = next;
              setSchedulerRange(next);
            }
            previousSchedulerAt.current = now;
            setLastSchedulerAt(now);
            setKnownLaneCount(Array.isArray(payload.knownCsrcs) ? payload.knownCsrcs.length : 0);
            return;
          }
          if (payload.type === "whisper-start") {
            const started = Number(payload.startedAtWallMs ?? Date.now());
            setLastWhisperStartAt(started);
            if (payload.trigger === "forced" || payload.forced === true) {
              setForcedWhisperStarts((count) => count + 1);
              return;
            }
            if (payload.trigger === "buffer") {
              setBufferWhisperStarts((count) => count + 1);
              return;
            }
            setOrdinaryWhisperStarts((count) => count + 1);
            const gap = Number(payload.ordinaryCallGapMs);
            if (Number.isFinite(gap) && gap >= 0) {
              setLastWhisperCallGapMs(gap);
              const prior = whisperCallRangeRef.current;
              const next = prior
                ? { min: Math.min(prior.min, gap), max: Math.max(prior.max, gap), samples: prior.samples + 1 }
                : { min: gap, max: gap, samples: 1 };
              whisperCallRangeRef.current = next;
              setWhisperCallRange(next);
            }
            return;
          }
          if (payload.type === "whisper") { setLastInferenceAt(Date.now()); return; }
          if (payload.type === "segment") {
            const row = payload.segment as PipelineRow;
            if (!row.completed && !row.text.trim()) raw.current.delete(row.segmentId);
            else raw.current.set(row.segmentId, row);
            if (row.completed && row.text.trim()) confirmed.current.add(row.segmentId);
            publish();
            return;
          }
          if (payload.type === "complete") setStatus("Complete");
          if (payload.type === "pipeline-error" || payload.type === "failed") {
            setStatus("Failed");
            setError(String(payload.message));
          }
        };
        stream.onerror = () => { if (status === "Ready") setStatus("Connecting…"); };
      })
      .catch((reason) => setError(reason instanceof Error ? reason.message : String(reason)));

    const follow = (): void => {
      if (startedAt.current != null) setCallPosition(60 + Math.max(0, (Date.now() - startedAt.current) / 1000));
      animation = requestAnimationFrame(follow);
    };
    animation = requestAnimationFrame(follow);
    return () => { closed = true; stream?.close(); cancelAnimationFrame(animation); };
  // This is one witness session. All mutable replay state deliberately lives in refs.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const start = async (): Promise<void> => {
    setError(null);
    setStatus("Starting…");
    if (audioRef.current) audioRef.current.currentTime = 0;
    const response = await fetch(`${API}/start`, { cache: "no-store" });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      setError(body.message ?? `start HTTP ${response.status}`);
      setStatus("Failed");
    }
  };

  const syncAudio = async (): Promise<void> => {
    const audio = audioRef.current;
    if (!audio) return;
    const position = startedAt.current == null ? 0 : Math.max(0, (Date.now() - startedAt.current) / 1000);
    audio.currentTime = Math.min(position, Number.isFinite(audio.duration) ? audio.duration : position);
    try {
      await audio.play();
      setAudioState("Listening");
    } catch (reason) {
      setAudioState("Press again to allow audio");
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  };

  const transcriptEdgeLagMs = transcriptEdgeMs != null && cutStartAt.current != null && startedAt.current != null
    ? Math.max(0, cutStartAt.current + (Date.now() - startedAt.current) - transcriptEdgeMs)
    : null;

  return (
    <div style={{ minHeight: "100vh", color: "var(--t1)", background: "var(--bg)" }}>
      <header style={{ position: "sticky", top: 0, zIndex: 5, background: "var(--bg)", borderBottom: "1px solid var(--line2)", padding: "12px 18px" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <strong>Microsoft Teams · m26123</strong>
          <span style={{ color: status === "LIVE PIPELINE" ? "var(--danger)" : "var(--t3)", fontSize: 12 }}>{status}</span>
          <button type="button" onClick={() => void start()} disabled={status !== "Ready" && status !== "Failed"}
            style={{ marginLeft: "auto", border: "1px solid var(--line2)", borderRadius: 8, padding: "5px 10px", background: "transparent", color: "var(--t1)", cursor: "pointer" }}>
            Join replayed call
          </button>
          <button type="button" onClick={() => void syncAudio()}
            style={{ border: "1px solid var(--line2)", borderRadius: 8, padding: "5px 10px", background: "transparent", color: "var(--t1)", cursor: "pointer" }}>
            {audioState === "Listening" ? "Sync audio" : "Listen / sync"}
          </button>
        </div>
        <audio ref={audioRef} src={AUDIO} preload="auto" />
        <div style={{ display: "grid", gridTemplateColumns: "repeat(6, minmax(120px, 1fr))", gap: 12, marginTop: 10, color: "var(--t3)", fontSize: 11 }}>
          <span>Call <b style={{ color: "var(--t1)" }}>{clock(callPosition)} / 21:00</b></span>
          <span>Transcript edge lag <b style={{ color: transcriptEdgeLagMs != null && transcriptEdgeLagMs <= 5000 ? "var(--green)" : "var(--warn)" }}>{latency(transcriptEdgeLagMs)}</b></span>
          <span>Replay clock <b style={{ color: "var(--t1)" }}>{lastSchedulerAt == null ? "waiting" : `${((Date.now() - lastSchedulerAt) / 1000).toFixed(1)}s ago · gap ${latency(schedulerGapMs)} · range ${schedulerRange ? `${(schedulerRange.min / 1000).toFixed(1)}–${(schedulerRange.max / 1000).toFixed(1)}s/${schedulerRange.samples}` : "—"} · ${knownLaneCount} known lanes`}</b></span>
          <span>Whisper min stop <b style={{ color: bufferWhisperStarts === 0 && (whisperCallRange?.min ?? lastWhisperCallGapMs ?? 2000) >= 1980 ? "var(--green)" : "var(--danger)" }}>{lastWhisperStartAt == null ? "waiting" : `${((Date.now() - lastWhisperStartAt) / 1000).toFixed(1)}s ago · actual min ${whisperCallRange ? `${(whisperCallRange.min / 1000).toFixed(2)}s` : latency(lastWhisperCallGapMs)} · ${ordinaryWhisperStarts}+${forcedWhisperStarts} forced+${bufferWhisperStarts} off-timer`}</b></span>
          <span>Whisper result <b style={{ color: "var(--t1)" }}>{lastInferenceAt == null ? "waiting" : `${((Date.now() - lastInferenceAt) / 1000).toFixed(1)}s ago`}</b></span>
          <span><b style={{ color: "var(--t1)" }}>{confirmedCount}</b> final · <b style={{ color: "var(--t1)" }}>{pendingCount}</b> draft · <b style={{ color: "var(--warn)" }}>{contestedCount}</b> contested</span>
        </div>
        <div style={{ marginTop: 6, color: "var(--t3)", fontSize: 11 }}>{identity}</div>
        {error && <div style={{ marginTop: 7, color: "var(--danger)", fontSize: 12 }}>{error}</div>}
      </header>
      <main style={{ padding: 18, overflow: "auto" }}>
        <LiveTranscriptEngine segments={segments} emptyLabel="Listening — waiting for the actual candidate pipeline…" />
      </main>
    </div>
  );
}
