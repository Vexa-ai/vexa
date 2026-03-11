"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import {
  CheckCircle2,
  XCircle,
  Pencil,
  Plus,
  ChevronDown,
  ChevronUp,
  Zap,
  ClipboardList,
  Cpu,
  Check,
  X,
} from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Textarea } from "@/components/ui/textarea";
import { Separator } from "@/components/ui/separator";
import { toast } from "sonner";
import { cn } from "@/lib/utils";

// ────────────────────────────────────────────────────────────────────────────
// Types
// ────────────────────────────────────────────────────────────────────────────

export type DecisionItemType =
  | "decision"
  | "action_item"
  | "architecture_statement";

export interface DecisionItem {
  id: string; // client-generated UUID
  type: DecisionItemType;
  summary: string;
  speaker?: string;
  confidence?: number;
  meeting_id?: string;
  status: "pending" | "approved" | "discarded";
  editedSummary?: string; // set when user edits before approving
  isManual?: boolean;
}

// ────────────────────────────────────────────────────────────────────────────
// Helpers
// ────────────────────────────────────────────────────────────────────────────

const DECISION_LISTENER_URL =
  process.env.NEXT_PUBLIC_DECISION_LISTENER_URL ?? "http://localhost:8765";

function uid() {
  return Math.random().toString(36).slice(2) + Date.now().toString(36);
}

/**
 * Jaccard similarity on significant words (>3 chars).
 * Returns 0–1. Threshold of 0.65 catches near-duplicate LLM phrasings.
 */
function wordSimilarity(a: string, b: string): number {
  const tokenize = (s: string) =>
    new Set(
      s
        .toLowerCase()
        .replace(/[^a-z0-9\s]/g, "")
        .split(/\s+/)
        .filter((w) => w.length > 3)
    );
  const wa = tokenize(a);
  const wb = tokenize(b);
  if (wa.size === 0 && wb.size === 0) return 1;
  const intersection = [...wa].filter((w) => wb.has(w)).length;
  const union = new Set([...wa, ...wb]).size;
  return union === 0 ? 0 : intersection / union;
}

const SIMILARITY_THRESHOLD = 0.65;

const TYPE_META: Record<
  DecisionItemType,
  { label: string; icon: React.ReactNode; badgeClass: string }
> = {
  decision: {
    label: "Decision",
    icon: <CheckCircle2 className="h-3.5 w-3.5" />,
    badgeClass: "bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-300",
  },
  action_item: {
    label: "Action Item",
    icon: <ClipboardList className="h-3.5 w-3.5" />,
    badgeClass: "bg-blue-100 text-blue-800 dark:bg-blue-900/40 dark:text-blue-300",
  },
  architecture_statement: {
    label: "Architecture",
    icon: <Cpu className="h-3.5 w-3.5" />,
    badgeClass: "bg-purple-100 text-purple-800 dark:bg-purple-900/40 dark:text-purple-300",
  },
};

// ────────────────────────────────────────────────────────────────────────────
// Notification sound (tiny inline beep)
// ────────────────────────────────────────────────────────────────────────────

function playNotificationSound() {
  try {
    const ctx = new AudioContext();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.frequency.value = 880;
    osc.type = "sine";
    gain.gain.setValueAtTime(0, ctx.currentTime);
    gain.gain.linearRampToValueAtTime(0.18, ctx.currentTime + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.35);
    osc.start(ctx.currentTime);
    osc.stop(ctx.currentTime + 0.35);
    osc.onended = () => ctx.close();
  } catch {
    // audio not available — silently ignore
  }
}

// ────────────────────────────────────────────────────────────────────────────
// Single item row
// ────────────────────────────────────────────────────────────────────────────

function DecisionRow({
  item,
  onApprove,
  onDiscard,
  onEdit,
}: {
  item: DecisionItem;
  onApprove: (id: string, summary: string) => void;
  onDiscard: (id: string) => void;
  onEdit: (id: string, summary: string) => void;
}) {
  const meta = TYPE_META[item.type];
  const [isEditing, setIsEditing] = useState(false);
  const [draft, setDraft] = useState(item.editedSummary ?? item.summary);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const handleEditStart = () => {
    setDraft(item.editedSummary ?? item.summary);
    setIsEditing(true);
    setTimeout(() => textareaRef.current?.focus(), 0);
  };

  const handleEditSave = () => {
    if (draft.trim()) {
      onEdit(item.id, draft.trim());
    }
    setIsEditing(false);
  };

  const handleEditCancel = () => {
    setDraft(item.editedSummary ?? item.summary);
    setIsEditing(false);
  };

  const displayText = item.editedSummary ?? item.summary;

  if (item.status === "discarded") return null;

  return (
    <div
      className={cn(
        "rounded-lg border p-3 space-y-2 transition-all",
        item.status === "approved"
          ? "bg-muted/30 opacity-70"
          : "bg-card"
      )}
    >
      {/* Header row */}
      <div className="flex items-center gap-2 flex-wrap">
        <span
          className={cn(
            "inline-flex items-center gap-1 text-[10px] font-semibold px-1.5 py-0.5 rounded-full",
            meta.badgeClass
          )}
        >
          {meta.icon}
          {meta.label}
        </span>
        {item.speaker && (
          <span className="text-[10px] text-muted-foreground">
            {item.speaker}
          </span>
        )}
        {item.confidence != null && (
          <span className="text-[10px] text-muted-foreground ml-auto">
            {Math.round(item.confidence * 100)}%
          </span>
        )}
        {item.isManual && (
          <Badge variant="outline" className="text-[9px] h-4 px-1 ml-auto">
            manual
          </Badge>
        )}
        {item.status === "approved" && (
          <Badge className="text-[9px] h-4 px-1 ml-auto bg-green-500 text-white">
            approved
          </Badge>
        )}
      </div>

      {/* Content */}
      {isEditing ? (
        <div className="space-y-2">
          <Textarea
            ref={textareaRef}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            className="min-h-[60px] text-sm resize-none"
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) handleEditSave();
              if (e.key === "Escape") handleEditCancel();
            }}
          />
          <div className="flex gap-1.5">
            <Button
              size="sm"
              className="h-7 text-xs gap-1"
              onClick={handleEditSave}
              disabled={!draft.trim()}
            >
              <Check className="h-3 w-3" />
              Save
            </Button>
            <Button
              size="sm"
              variant="ghost"
              className="h-7 text-xs gap-1"
              onClick={handleEditCancel}
            >
              <X className="h-3 w-3" />
              Cancel
            </Button>
          </div>
        </div>
      ) : (
        <p className="text-sm leading-snug">{displayText}</p>
      )}

      {/* Actions — only show when pending */}
      {item.status === "pending" && !isEditing && (
        <div className="flex gap-1.5 pt-0.5">
          <Button
            size="sm"
            className="h-7 text-xs gap-1 bg-green-600 hover:bg-green-700 text-white"
            onClick={() => onApprove(item.id, item.editedSummary ?? item.summary)}
          >
            <CheckCircle2 className="h-3 w-3" />
            Approve
          </Button>
          <Button
            size="sm"
            variant="outline"
            className="h-7 text-xs gap-1"
            onClick={handleEditStart}
          >
            <Pencil className="h-3 w-3" />
            Edit
          </Button>
          <Button
            size="sm"
            variant="ghost"
            className="h-7 text-xs gap-1 text-destructive hover:text-destructive"
            onClick={() => onDiscard(item.id)}
          >
            <XCircle className="h-3 w-3" />
            Discard
          </Button>
        </div>
      )}
    </div>
  );
}

// ────────────────────────────────────────────────────────────────────────────
// Manual add form
// ────────────────────────────────────────────────────────────────────────────

function AddManualForm({ onAdd }: { onAdd: (item: Omit<DecisionItem, "id" | "status">) => void }) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [type, setType] = useState<DecisionItemType>("decision");

  const handleSubmit = () => {
    const trimmed = text.trim();
    if (!trimmed) return;
    onAdd({ type, summary: trimmed, isManual: true });
    setText("");
    setOpen(false);
  };

  if (!open) {
    return (
      <Button
        variant="outline"
        size="sm"
        className="w-full h-8 text-xs gap-1.5 mt-1"
        onClick={() => setOpen(true)}
      >
        <Plus className="h-3.5 w-3.5" />
        Add manually
      </Button>
    );
  }

  return (
    <div className="rounded-lg border p-3 space-y-2 bg-muted/20">
      <div className="flex gap-1.5 flex-wrap">
        {(["decision", "action_item", "architecture_statement"] as DecisionItemType[]).map(
          (t) => (
            <button
              key={t}
              onClick={() => setType(t)}
              className={cn(
                "inline-flex items-center gap-1 text-[10px] font-semibold px-1.5 py-0.5 rounded-full transition-opacity",
                TYPE_META[t].badgeClass,
                type !== t && "opacity-40"
              )}
            >
              {TYPE_META[t].icon}
              {TYPE_META[t].label}
            </button>
          )
        )}
      </div>
      <Textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="Describe the decision, action item, or architecture note…"
        className="min-h-[70px] text-sm resize-none"
        autoFocus
        onKeyDown={(e) => {
          if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) handleSubmit();
          if (e.key === "Escape") setOpen(false);
        }}
      />
      <div className="flex gap-1.5">
        <Button size="sm" className="h-7 text-xs" onClick={handleSubmit} disabled={!text.trim()}>
          Add
        </Button>
        <Button size="sm" variant="ghost" className="h-7 text-xs" onClick={() => setOpen(false)}>
          Cancel
        </Button>
      </div>
    </div>
  );
}

// ────────────────────────────────────────────────────────────────────────────
// Main panel
// ────────────────────────────────────────────────────────────────────────────

interface DecisionsPanelProps {
  meetingId: string;
  isActive: boolean; // whether meeting is live
  embedded?: boolean; // when true, renders without Card wrapper / collapse toggle
}

export function DecisionsPanel({ meetingId, isActive, embedded }: DecisionsPanelProps) {
  const [items, setItems] = useState<DecisionItem[]>([]);
  const [isCollapsed, setIsCollapsed] = useState(false);
  const [connected, setConnected] = useState(false);
  const esRef = useRef<EventSource | null>(null);
  // Keep a ref to current items for use inside SSE handler without stale closure
  const itemsRef = useRef<DecisionItem[]>([]);
  itemsRef.current = items;

  // ── SSE connection ──────────────────────────────────────────────────────

  const connectSSE = useCallback(() => {
    if (esRef.current) return; // already connected
    const url = `${DECISION_LISTENER_URL}/decisions/${meetingId}`;
    const es = new EventSource(url);
    esRef.current = es;

    es.onopen = () => setConnected(true);

    es.onmessage = (ev) => {
      try {
        const data = JSON.parse(ev.data);
        if (!data.type || data.type === "no_match") return;

        const incomingSummary = (data.summary ?? "").trim();

        // Fuzzy dedup: skip if a non-discarded item of same type is already similar
        const alreadyExists = itemsRef.current
          .filter((it) => it.status !== "discarded" && it.type === data.type)
          .some((it) =>
            wordSimilarity(it.editedSummary ?? it.summary, incomingSummary) >= SIMILARITY_THRESHOLD
          );
        if (alreadyExists) return;

        const newItem: DecisionItem = {
          id: uid(),
          type: data.type as DecisionItemType,
          summary: incomingSummary,
          speaker: data.speaker,
          confidence: data.confidence,
          meeting_id: data.meeting_id,
          status: "pending",
        };

        setItems((prev) => [newItem, ...prev]);
        playNotificationSound();
        toast.success(`New ${TYPE_META[newItem.type].label} detected`, {
          description:
            newItem.summary.length > 90
              ? newItem.summary.slice(0, 90) + "…"
              : newItem.summary,
          duration: 6000,
        });
      } catch {
        // ignore parse errors
      }
    };

    es.onerror = () => {
      setConnected(false);
      es.close();
      esRef.current = null;
      // Reconnect after 5 s if still active
      setTimeout(() => {
        if (isActive) connectSSE();
      }, 5000);
    };
  }, [meetingId, isActive]);

  useEffect(() => {
    if (!isActive) return;
    connectSSE();
    return () => {
      esRef.current?.close();
      esRef.current = null;
    };
  }, [isActive, connectSSE]);

  // ── Load existing decisions on mount ────────────────────────────────────

  useEffect(() => {
    const load = async () => {
      try {
        const res = await fetch(
          `${DECISION_LISTENER_URL}/decisions/${meetingId}/all`
        );
        if (!res.ok) return;
        const data = await res.json();
        const loaded: DecisionItem[] = (data.items ?? [])
          .filter((d: { type?: string }) => d.type && d.type !== "no_match")
          .map((d: {
            type: DecisionItemType;
            summary?: string;
            speaker?: string;
            confidence?: number;
          }) => ({
            id: uid(),
            type: d.type,
            summary: d.summary ?? "",
            speaker: d.speaker,
            confidence: d.confidence,
            status: "approved" as const,
          }));
        if (loaded.length > 0) {
          setItems(loaded.reverse()); // oldest first → newest on top after state set
        }
      } catch {
        // silently ignore if listener not available
      }
    };
    load();
  }, [meetingId]);

  // ── Item actions ─────────────────────────────────────────────────────────

  const handleApprove = (id: string, _summary: string) => {
    setItems((prev) =>
      prev.map((it) => (it.id === id ? { ...it, status: "approved" } : it))
    );
    toast.success("Decision approved");
  };

  const handleDiscard = (id: string) => {
    setItems((prev) =>
      prev.map((it) => (it.id === id ? { ...it, status: "discarded" } : it))
    );
  };

  const handleEdit = (id: string, summary: string) => {
    setItems((prev) =>
      prev.map((it) => (it.id === id ? { ...it, editedSummary: summary } : it))
    );
  };

  const handleAddManual = (partial: Omit<DecisionItem, "id" | "status">) => {
    const newItem: DecisionItem = {
      ...partial,
      id: uid(),
      status: "pending",
    };
    setItems((prev) => [newItem, ...prev]);
  };

  // ── Derived counts ───────────────────────────────────────────────────────

  const visibleItems = items.filter((it) => it.status !== "discarded");
  const pendingCount = items.filter((it) => it.status === "pending").length;

  // ── Status indicator (shared) ─────────────────────────────────────────────

  const statusIndicator = isActive && (
    <span
      className={cn(
        "inline-flex items-center gap-1 text-[10px] font-medium",
        connected ? "text-green-600" : "text-muted-foreground"
      )}
    >
      <span
        className={cn(
          "h-1.5 w-1.5 rounded-full",
          connected ? "bg-green-500 animate-pulse" : "bg-muted-foreground"
        )}
      />
      {connected ? "live" : "connecting…"}
    </span>
  );

  // ── Embedded mode (inside slide-over panel) ───────────────────────────────

  if (embedded) {
    return (
      <div className="flex flex-col h-full space-y-3">
        {/* Status bar */}
        <div className="flex items-center justify-between">
          {statusIndicator}
          {pendingCount > 0 && (
            <span className="inline-flex items-center justify-center h-5 min-w-[20px] px-1.5 rounded-full bg-amber-500 text-[10px] font-bold text-white">
              {pendingCount} pending
            </span>
          )}
        </div>

        {/* Items */}
        <div className="flex-1 space-y-2 overflow-y-auto min-h-0">
          {visibleItems.length === 0 ? (
            <p className="text-xs text-muted-foreground italic">
              {isActive
                ? "Listening for decisions, action items, and architecture statements…"
                : "No items detected."}
            </p>
          ) : (
            visibleItems.map((item) => (
              <DecisionRow
                key={item.id}
                item={item}
                onApprove={handleApprove}
                onDiscard={handleDiscard}
                onEdit={handleEdit}
              />
            ))
          )}
        </div>

        {visibleItems.length > 0 && <Separator />}
        <AddManualForm onAdd={handleAddManual} />
      </div>
    );
  }

  // ── Card mode (legacy / sidebar widget) ──────────────────────────────────

  return (
    <Card className={cn(isActive && pendingCount > 0 && "border-amber-400/60 shadow-amber-400/10 shadow-md")}>
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <CardTitle className="flex items-center gap-2 text-sm">
            <Zap className="h-4 w-4 text-amber-500" />
            Decisions
            {pendingCount > 0 && (
              <span className="inline-flex items-center justify-center h-4 min-w-[16px] px-1 rounded-full bg-amber-500 text-[9px] font-bold text-white">
                {pendingCount}
              </span>
            )}
          </CardTitle>
          <div className="flex items-center gap-1.5">
            {statusIndicator}
            <Button
              variant="ghost"
              size="icon"
              className="h-6 w-6"
              onClick={() => setIsCollapsed((v) => !v)}
            >
              {isCollapsed ? (
                <ChevronDown className="h-3.5 w-3.5" />
              ) : (
                <ChevronUp className="h-3.5 w-3.5" />
              )}
            </Button>
          </div>
        </div>
      </CardHeader>

      {!isCollapsed && (
        <CardContent className="space-y-2 pt-0">
          {visibleItems.length === 0 ? (
            <p className="text-xs text-muted-foreground italic">
              {isActive
                ? "Listening for decisions, action items, and architecture statements…"
                : "No items detected."}
            </p>
          ) : (
            <div className="space-y-2 max-h-[420px] overflow-y-auto pr-0.5">
              {visibleItems.map((item) => (
                <DecisionRow
                  key={item.id}
                  item={item}
                  onApprove={handleApprove}
                  onDiscard={handleDiscard}
                  onEdit={handleEdit}
                />
              ))}
            </div>
          )}

          {visibleItems.length > 0 && <Separator />}

          <AddManualForm onAdd={handleAddManual} />
        </CardContent>
      )}
    </Card>
  );
}
