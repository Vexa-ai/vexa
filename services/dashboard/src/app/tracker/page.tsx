"use client";

import { useState, useEffect, useCallback } from "react";
import { redirect } from "next/navigation";
import {
  Zap,
  Plus,
  Trash2,
  RotateCcw,
  Save,
  Loader2,
  GripVertical,
  AlertCircle,
} from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { Badge } from "@/components/ui/badge";
import { toast } from "sonner";
import { cn } from "@/lib/utils";

const DECISION_LISTENER_URL =
  process.env.NEXT_PUBLIC_DECISION_LISTENER_URL ?? "http://localhost:8765";

// ── Types ──────────────────────────────────────────────────────────────────────

interface TrackerCategory {
  key: string;
  label: string;
  description: string;
  enabled: boolean;
}

interface TrackerConfig {
  name: string;
  description: string;
  categories: TrackerCategory[];
  extra_instructions: string;
}

// ── Colour palette for category badges ────────────────────────────────────────

const BADGE_COLORS = [
  "bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-300",
  "bg-blue-100 text-blue-800 dark:bg-blue-900/40 dark:text-blue-300",
  "bg-purple-100 text-purple-800 dark:bg-purple-900/40 dark:text-purple-300",
  "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300",
  "bg-rose-100 text-rose-800 dark:bg-rose-900/40 dark:text-rose-300",
  "bg-cyan-100 text-cyan-800 dark:bg-cyan-900/40 dark:text-cyan-300",
];

// ── Category row ──────────────────────────────────────────────────────────────

function CategoryRow({
  cat,
  index,
  colorClass,
  onChange,
  onRemove,
}: {
  cat: TrackerCategory;
  index: number;
  colorClass: string;
  onChange: (updated: TrackerCategory) => void;
  onRemove: () => void;
}) {
  return (
    <div className="rounded-lg border bg-card p-4 space-y-3">
      <div className="flex items-center gap-3">
        <GripVertical className="h-4 w-4 text-muted-foreground shrink-0 cursor-grab" />

        <span className={cn("inline-flex items-center text-[11px] font-semibold px-2 py-0.5 rounded-full shrink-0", colorClass)}>
          {cat.label || `Category ${index + 1}`}
        </span>

        <div className="flex-1 min-w-0">
          <Input
            value={cat.key}
            onChange={(e) => onChange({ ...cat, key: e.target.value.toLowerCase().replace(/\s+/g, "_") })}
            placeholder="key_name"
            className="h-7 text-xs font-mono"
          />
        </div>

        <div className="flex items-center gap-2 shrink-0">
          <Button
            variant={cat.enabled ? "default" : "outline"}
            size="sm"
            className="h-6 px-2 text-[10px]"
            onClick={() => onChange({ ...cat, enabled: !cat.enabled })}
          >
            {cat.enabled ? "On" : "Off"}
          </Button>
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7 text-destructive hover:text-destructive"
            onClick={onRemove}
          >
            <Trash2 className="h-3.5 w-3.5" />
          </Button>
        </div>
      </div>

      <div className="space-y-2 pl-7">
        <Input
          value={cat.label}
          onChange={(e) => onChange({ ...cat, label: e.target.value })}
          placeholder="Display label (e.g. Decision)"
          className="h-8 text-sm"
        />
        <Textarea
          value={cat.description}
          onChange={(e) => onChange({ ...cat, description: e.target.value })}
          placeholder={`Describe when to capture a "${cat.key}" (used in the LLM prompt)`}
          className="min-h-[60px] text-sm resize-none"
          rows={2}
        />
      </div>
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function TrackerPage() {
  if (process.env.NEXT_PUBLIC_TRACKER_ENABLED !== "true") {
    redirect("/");
  }

  const [config, setConfig] = useState<TrackerConfig | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [isResetting, setIsResetting] = useState(false);
  const [listenerStatus, setListenerStatus] = useState<"unknown" | "online" | "offline">("unknown");

  // ── Fetch current config from listener ──────────────────────────────────────

  const fetchConfig = useCallback(async () => {
    try {
      const res = await fetch(`${DECISION_LISTENER_URL}/config`);
      if (!res.ok) throw new Error("fetch failed");
      const data: TrackerConfig = await res.json();
      setConfig(data);
      setListenerStatus("online");
    } catch {
      setListenerStatus("offline");
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchConfig();
  }, [fetchConfig]);

  // ── Persist to listener ─────────────────────────────────────────────────────

  const handleSave = async () => {
    if (!config) return;
    setIsSaving(true);
    try {
      const res = await fetch(`${DECISION_LISTENER_URL}/config`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(config),
      });
      if (!res.ok) throw new Error(await res.text());
      const updated: TrackerConfig = await res.json();
      setConfig(updated);
      toast.success("Tracker config saved", {
        description: "The listener will use the new config on the next LLM call.",
      });
    } catch (e) {
      toast.error("Failed to save config", { description: String(e) });
    } finally {
      setIsSaving(false);
    }
  };

  const handleReset = async () => {
    setIsResetting(true);
    try {
      const res = await fetch(`${DECISION_LISTENER_URL}/config/reset`, { method: "POST" });
      if (!res.ok) throw new Error(await res.text());
      const defaults: TrackerConfig = await res.json();
      setConfig(defaults);
      toast.success("Reset to defaults");
    } catch (e) {
      toast.error("Failed to reset", { description: String(e) });
    } finally {
      setIsResetting(false);
    }
  };

  // ── Category helpers ─────────────────────────────────────────────────────────

  const updateCategory = (i: number, updated: TrackerCategory) => {
    if (!config) return;
    const cats = [...config.categories];
    cats[i] = updated;
    setConfig({ ...config, categories: cats });
  };

  const removeCategory = (i: number) => {
    if (!config) return;
    const cats = config.categories.filter((_, idx) => idx !== i);
    setConfig({ ...config, categories: cats });
  };

  const addCategory = () => {
    if (!config) return;
    const newCat: TrackerCategory = {
      key: `category_${config.categories.length + 1}`,
      label: `Category ${config.categories.length + 1}`,
      description: "Describe what to capture",
      enabled: true,
    };
    setConfig({ ...config, categories: [...config.categories, newCat] });
  };

  // ── Render ───────────────────────────────────────────────────────────────────

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 text-muted-foreground py-12 justify-center">
        <Loader2 className="h-5 w-5 animate-spin" />
        Loading tracker config…
      </div>
    );
  }

  return (
    <div className="space-y-6 max-w-2xl">
      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-[-0.02em] text-foreground flex items-center gap-2">
            <Zap className="h-7 w-7 text-amber-500" />
            Tracker Config
          </h1>
          <p className="text-muted-foreground mt-1">
            Define what the real-time listener detects during meetings. Changes take effect immediately — no restart needed.
          </p>
        </div>

        <div className="flex items-center gap-2 shrink-0 pt-1">
          <span
            className={cn(
              "inline-flex items-center gap-1.5 text-xs font-medium px-2 py-1 rounded-full",
              listenerStatus === "online"
                ? "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400"
                : listenerStatus === "offline"
                ? "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400"
                : "bg-muted text-muted-foreground"
            )}
          >
            <span className={cn(
              "h-1.5 w-1.5 rounded-full",
              listenerStatus === "online" ? "bg-green-500" : "bg-red-400"
            )} />
            Listener {listenerStatus === "online" ? "online" : listenerStatus === "offline" ? "offline" : "…"}
          </span>
        </div>
      </div>

      {listenerStatus === "offline" && (
        <Card className="border-destructive/40 bg-destructive/5">
          <CardContent className="pt-4 flex items-center gap-3 text-sm">
            <AlertCircle className="h-4 w-4 text-destructive shrink-0" />
            <span>
              Decision listener is offline at <code className="bg-muted px-1 rounded">{DECISION_LISTENER_URL}</code>.
              Start it first, then reload this page.
            </span>
          </CardContent>
        </Card>
      )}

      {config && (
        <>
          {/* General */}
          <Card>
            <CardHeader>
              <CardTitle>General</CardTitle>
              <CardDescription>Name and description for this tracker configuration.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="space-y-1.5">
                <Label htmlFor="tracker-name">Tracker name</Label>
                <Input
                  id="tracker-name"
                  value={config.name}
                  onChange={(e) => setConfig({ ...config, name: e.target.value })}
                  placeholder="e.g. Meeting Intelligence"
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="tracker-desc">Description</Label>
                <Input
                  id="tracker-desc"
                  value={config.description}
                  onChange={(e) => setConfig({ ...config, description: e.target.value })}
                  placeholder="One-line description of what this tracker captures"
                />
              </div>
            </CardContent>
          </Card>

          {/* Categories */}
          <Card>
            <CardHeader>
              <div className="flex items-center justify-between">
                <div>
                  <CardTitle>Categories</CardTitle>
                  <CardDescription className="mt-1">
                    Each category maps to a type the LLM can emit. Disable to hide without deleting.
                  </CardDescription>
                </div>
                <Badge variant="outline" className="shrink-0">
                  {config.categories.filter((c) => c.enabled).length} active
                </Badge>
              </div>
            </CardHeader>
            <CardContent className="space-y-3">
              {config.categories.map((cat, i) => (
                <CategoryRow
                  key={i}
                  cat={cat}
                  index={i}
                  colorClass={BADGE_COLORS[i % BADGE_COLORS.length]}
                  onChange={(updated) => updateCategory(i, updated)}
                  onRemove={() => removeCategory(i)}
                />
              ))}
              <Button variant="outline" size="sm" className="w-full gap-1.5 h-9" onClick={addCategory}>
                <Plus className="h-4 w-4" />
                Add category
              </Button>
            </CardContent>
          </Card>

          {/* Extra instructions */}
          <Card>
            <CardHeader>
              <CardTitle>Extra Instructions</CardTitle>
              <CardDescription>
                Additional rules appended to the LLM system prompt. Use plain prose — each sentence becomes a bullet point.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <Textarea
                value={config.extra_instructions}
                onChange={(e) => setConfig({ ...config, extra_instructions: e.target.value })}
                placeholder="e.g. Be conservative. Only capture explicit agreements, not tentative suggestions."
                className="min-h-[100px] resize-none text-sm"
              />
            </CardContent>
          </Card>

          {/* Preview */}
          <Card>
            <CardHeader>
              <CardTitle className="text-sm">System Prompt Preview</CardTitle>
              <CardDescription>What the LLM sees (reconstructed from your settings above).</CardDescription>
            </CardHeader>
            <CardContent>
              <pre className="text-xs bg-muted rounded-md p-3 whitespace-pre-wrap font-mono overflow-auto max-h-64">
                {buildPreviewPrompt(config)}
              </pre>
            </CardContent>
          </Card>

          {/* Actions */}
          <div className="flex items-center gap-3 pb-8">
            <Button onClick={handleSave} disabled={isSaving || listenerStatus === "offline"} className="gap-2">
              {isSaving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
              Save & Apply
            </Button>
            <Button
              variant="outline"
              onClick={handleReset}
              disabled={isResetting || listenerStatus === "offline"}
              className="gap-2"
            >
              {isResetting ? <Loader2 className="h-4 w-4 animate-spin" /> : <RotateCcw className="h-4 w-4" />}
              Reset to defaults
            </Button>
          </div>
        </>
      )}
    </div>
  );
}

// ── Helper: build a preview of the system prompt from local state ─────────────

function buildPreviewPrompt(cfg: TrackerConfig): string {
  const enabled = cfg.categories.filter((c) => c.enabled);
  const lines = [
    "You are a precise meeting analyst.",
    "You are given a rolling window of recent transcript segments from a live meeting.",
    "",
    "Your job: detect exactly ONE of the following, if present:",
  ];
  for (const cat of enabled) {
    lines.push(`- **${cat.key}**: ${cat.description}`);
  }
  lines.push("- **no_match**: nothing significant to capture right now");
  lines.push("");
  lines.push("Rules:");
  for (const rule of cfg.extra_instructions.split(". ")) {
    const r = rule.trim();
    if (r) lines.push(`- ${r}.`);
  }
  lines.push("- Always call capture_meeting_item — even for no_match.");
  return lines.join("\n");
}
