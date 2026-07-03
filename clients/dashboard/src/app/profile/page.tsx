"use client";

import { useState, useEffect } from "react";
import {
  User,
  Key,
  Copy,
  Loader2,
  Plus,
  Check,
  GitBranch,
  RefreshCw,
} from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { toast } from "sonner";
import { useAuthStore } from "@/stores/auth-store";
import { cn } from "@/lib/utils";
import { withBasePath } from "@/lib/base-path";

// ==========================================
// Types
// ==========================================

interface APIKeyDisplay {
  id: string;
  name: string;
  scopes: KeyScope[];
  token: string;
  masked_token: string;
  created_at: string;
  last_used_at: string | null;
  expires_at: string | null;
}

type KeyScope = "bot" | "tx" | "browser";

const SCOPE_CONFIG: Record<KeyScope, { label: string; prefix: string; color: string; bgColor: string }> = {
  bot: { label: "bot", prefix: "vxa_bot_", color: "text-purple-300", bgColor: "bg-purple-900/40" },
  tx: { label: "tx", prefix: "vxa_tx_", color: "text-cyan-300", bgColor: "bg-cyan-900/40" },
  browser: { label: "browser", prefix: "vxa_browser_", color: "text-emerald-300", bgColor: "bg-emerald-900/40" },
};

// ==========================================
// Helpers
// ==========================================

function inferScope(token: string): KeyScope {
  if (token.startsWith("vxa_tx_")) return "tx";
  if (token.startsWith("vxa_browser_")) return "browser";
  return "bot";
}

function maskToken(token: string): string {
  if (token.length < 16) return token;
  // Find prefix end (after vxa_xxx_)
  const prefixMatch = token.match(/^(vxa_\w+_)/);
  if (prefixMatch) {
    const prefix = prefixMatch[1];
    const rest = token.slice(prefix.length);
    if (rest.length >= 8) {
      return `${prefix}${rest.slice(0, 4)}••••${rest.slice(-4)}`;
    }
    return `${prefix}${rest}`;
  }
  return `${token.slice(0, 8)}••••${token.slice(-4)}`;
}

function relativeTime(dateStr: string): string {
  const diff = Date.now() - new Date(dateStr).getTime();
  const seconds = Math.floor(diff / 1000);
  if (seconds < 60) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d ago`;
  return new Date(dateStr).toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

function formatExpiry(dateStr: string): string {
  return new Date(dateStr).toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

function scopesFromApi(scopes: string[]): KeyScope[] {
  const valid: KeyScope[] = ["bot", "tx", "browser"];
  const result = scopes.filter((s): s is KeyScope => valid.includes(s as KeyScope));
  return result.length > 0 ? result : ["bot"];
}

// ==========================================
// Component
// ==========================================

export default function ProfilePage() {
  const user = useAuthStore((state) => state.user);

  // API Keys state
  const [apiKeys, setApiKeys] = useState<APIKeyDisplay[]>([]);
  const [isLoadingKeys, setIsLoadingKeys] = useState(true);
  const [showCreateDialog, setShowCreateDialog] = useState(false);
  const [newKeyName, setNewKeyName] = useState("");
  const [newKeyScopes, setNewKeyScopes] = useState<Set<KeyScope>>(new Set(["bot", "tx", "browser"]));
  const [newKeyExpiry, setNewKeyExpiry] = useState<string>("");
  const [isCreatingKey, setIsCreatingKey] = useState(false);
  const [createdKeyToken, setCreatedKeyToken] = useState<string | null>(null);
  const [copiedKeyId, setCopiedKeyId] = useState<string | null>(null);


  // Fetch API keys
  useEffect(() => {
    async function fetchKeys() {
      if (!user?.id) return;
      try {
        const response = await fetch(withBasePath(`/api/profile/keys?userId=${user.id}`));
        if (!response.ok) {
          // Graceful fallback — endpoint may not exist yet
          setApiKeys([]);
          return;
        }
        const data = await response.json();
        setApiKeys(
          (data.keys || []).map((k: { id: string; token: string; scopes?: string[]; name?: string; created_at: string; last_used_at?: string; expires_at?: string }) => ({
            id: k.id,
            name: k.name || "API Key",
            scopes: k.scopes && k.scopes.length > 0 ? scopesFromApi(k.scopes) : [inferScope(k.token)],
            token: k.token,
            masked_token: maskToken(k.token),
            created_at: k.created_at,
            last_used_at: k.last_used_at || null,
            expires_at: k.expires_at || null,
          }))
        );
      } catch {
        setApiKeys([]);
      } finally {
        setIsLoadingKeys(false);
      }
    }
    fetchKeys();
  }, [user?.id]);


  const handleCreateKey = async () => {
    setIsCreatingKey(true);
    try {
      const scopes = Array.from(newKeyScopes).join(",");
      const response = await fetch(withBasePath("/api/profile/keys"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: newKeyName,
          scopes,
          userId: user?.id,
          ...(newKeyExpiry ? { expires_in: parseInt(newKeyExpiry) * 86400 } : {}),
        }),
      });
      if (!response.ok) throw new Error("Failed to create key");
      const data = await response.json();
      setCreatedKeyToken(data.token);
      // Add to list
      setApiKeys((prev) => [
        ...prev,
        {
          id: data.id || String(Date.now()),
          name: newKeyName || "API Key",
          scopes: data.scopes ? scopesFromApi(data.scopes) : Array.from(newKeyScopes),
          token: data.token,
          masked_token: maskToken(data.token),
          created_at: new Date().toISOString(),
          last_used_at: null,
          expires_at: null,
        },
      ]);
      toast.success("API key created");
    } catch (error) {
      toast.error("Failed to create API key", { description: (error as Error).message });
    } finally {
      setIsCreatingKey(false);
    }
  };

  const handleRevokeKey = async (keyId: string) => {
    try {
      const response = await fetch(withBasePath(`/api/profile/keys/${keyId}`), { method: "DELETE" });
      if (!response.ok) throw new Error("Failed to revoke key");
      setApiKeys((prev) => prev.filter((k) => k.id !== keyId));
      toast.success("API key revoked");
    } catch (error) {
      toast.error("Failed to revoke key", { description: (error as Error).message });
    }
  };

  const handleCopyKey = async (keyId: string, token: string) => {
    await navigator.clipboard.writeText(token);
    setCopiedKeyId(keyId);
    setTimeout(() => setCopiedKeyId(null), 2000);
    toast.success("Copied to clipboard");
  };


  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-semibold tracking-[-0.02em] text-foreground">Profile</h1>
        <p className="text-sm text-muted-foreground">
          Manage your account and API keys
        </p>
      </div>

      <div className="max-w-2xl space-y-6">
        {/* Account info */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <User className="h-5 w-5" />
              Account
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3 text-sm">
            <div className="flex justify-between">
              <span className="text-muted-foreground">Email</span>
              <span>{user?.email || "—"}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted-foreground">Name</span>
              <span>{user?.name || "—"}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted-foreground">Max bots</span>
              <span>{user?.max_concurrent_bots ?? "—"} concurrent</span>
            </div>
          </CardContent>
        </Card>

        {/* API Keys */}
        <Card>
          <CardHeader>
            <div className="flex items-center justify-between">
              <CardTitle className="flex items-center gap-2">
                <Key className="h-5 w-5" />
                API Keys
              </CardTitle>
              <Button
                size="sm"
                onClick={() => {
                  setNewKeyName("");
                  setNewKeyScopes(new Set(["bot", "tx", "browser"]));
                  setNewKeyExpiry("");
                  setCreatedKeyToken(null);
                  setShowCreateDialog(true);
                }}
              >
                <Plus className="h-4 w-4 mr-1" />
                Create Key
              </Button>
            </div>
          </CardHeader>
          <CardContent>
            {isLoadingKeys ? (
              <div className="flex items-center justify-center py-8">
                <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
              </div>
            ) : apiKeys.length === 0 ? (
              <p className="text-sm text-muted-foreground py-4 text-center">
                No API keys yet. Create one to get started.
              </p>
            ) : (
              <div className="space-y-2">
                {apiKeys.map((key) => (
                  <div
                    key={key.id}
                    className="rounded-lg bg-muted/50 px-4 py-3 flex items-center justify-between"
                  >
                    <div>
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-medium">{key.name}</span>
                        {key.scopes.map((s) => (
                          <span
                            key={s}
                            className={cn(
                              "inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold",
                              SCOPE_CONFIG[s].bgColor,
                              SCOPE_CONFIG[s].color
                            )}
                          >
                            {SCOPE_CONFIG[s].label}
                          </span>
                        ))}
                      </div>
                      <p className="text-[11px] font-mono text-muted-foreground mt-0.5">
                        {key.masked_token}
                      </p>
                    </div>
                    <div className="flex items-center gap-3 text-xs">
                      <span className="text-muted-foreground" title="Last used">
                        {key.last_used_at ? relativeTime(key.last_used_at) : "Never used"}
                      </span>
                      <span className="text-muted-foreground" title="Expires">
                        {key.expires_at ? `Exp ${formatExpiry(key.expires_at)}` : "No expiry"}
                      </span>
                      <button
                        onClick={() => handleCopyKey(key.id, key.token)}
                        className="text-muted-foreground hover:text-foreground transition-colors"
                      >
                        {copiedKeyId === key.id ? (
                          <Check className="h-3.5 w-3.5 text-emerald-400" />
                        ) : (
                          <Copy className="h-3.5 w-3.5" />
                        )}
                      </button>
                      <button
                        onClick={() => handleRevokeKey(key.id)}
                        className="text-red-400 hover:text-red-300 transition-colors"
                      >
                        Revoke
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>

      </div>

      {/* Create Key Dialog */}
      <Dialog open={showCreateDialog} onOpenChange={setShowCreateDialog}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Create API Key</DialogTitle>
            <DialogDescription>
              Choose a key type and name for your new API key.
            </DialogDescription>
          </DialogHeader>

          {createdKeyToken ? (
            <div className="space-y-4">
              <div className="rounded-lg bg-emerald-950/30 border border-emerald-800/30 p-4">
                <p className="text-sm font-medium text-emerald-300 mb-2">
                  Key created successfully
                </p>
                <p className="text-xs text-muted-foreground mb-3">
                  Copy this key now. You will not be able to see it again.
                </p>
                <div className="flex items-center gap-2">
                  <code className="flex-1 bg-muted rounded px-3 py-2 text-xs font-mono break-all">
                    {createdKeyToken}
                  </code>
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => {
                      navigator.clipboard.writeText(createdKeyToken);
                      toast.success("Copied to clipboard");
                    }}
                  >
                    <Copy className="h-4 w-4" />
                  </Button>
                </div>
              </div>
              <DialogFooter>
                <Button onClick={() => setShowCreateDialog(false)}>Done</Button>
              </DialogFooter>
            </div>
          ) : (
            <div className="space-y-4">
              <div className="space-y-2">
                <Label>Key Name</Label>
                <Input
                  placeholder="e.g. Production Bot Key"
                  value={newKeyName}
                  onChange={(e) => setNewKeyName(e.target.value)}
                />
              </div>

              <div className="space-y-2">
                <Label>Scopes</Label>
                <div className="space-y-2">
                  {(["bot", "tx", "browser"] as const).map((scope) => {
                    const config = {
                      bot: { name: "Bot", desc: "Meeting bots, webhooks, voice agent" },
                      tx: { name: "Transcript", desc: "Read transcripts & meeting data" },
                      browser: { name: "Browser", desc: "Browser sessions, VNC, CDP, workspace" },
                    }[scope];
                    const checked = newKeyScopes.has(scope);
                    return (
                      <button
                        key={scope}
                        type="button"
                        onClick={() => {
                          setNewKeyScopes((prev) => {
                            const next = new Set(prev);
                            if (next.has(scope)) {
                              next.delete(scope);
                            } else {
                              next.add(scope);
                            }
                            return next;
                          });
                        }}
                        className={cn(
                          "w-full p-3 rounded-lg border-2 text-left transition-all flex items-center gap-3",
                          checked
                            ? "border-foreground/20 bg-muted/50"
                            : "border-border hover:border-muted-foreground/30"
                        )}
                      >
                        <div className={cn(
                          "h-4 w-4 rounded border-2 flex items-center justify-center flex-shrink-0",
                          checked ? "border-foreground bg-foreground" : "border-muted-foreground/40"
                        )}>
                          {checked && <Check className="h-3 w-3 text-background" />}
                        </div>
                        <div className="flex-1">
                          <div className="flex items-center gap-2">
                            <span className="text-sm font-medium">{config.name}</span>
                            <span
                              className={cn(
                                "inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold",
                                SCOPE_CONFIG[scope].bgColor,
                                SCOPE_CONFIG[scope].color
                              )}
                            >
                              {SCOPE_CONFIG[scope].label}
                            </span>
                          </div>
                          <p className="text-[11px] text-muted-foreground">
                            {config.desc}
                          </p>
                        </div>
                      </button>
                    );
                  })}
                </div>
              </div>

              <div className="space-y-2">
                <Label>Expiration</Label>
                <div className="grid grid-cols-4 gap-2">
                  {[
                    { label: "Never", value: "" },
                    { label: "30 days", value: "30" },
                    { label: "90 days", value: "90" },
                    { label: "1 year", value: "365" },
                  ].map((opt) => (
                    <button
                      key={opt.value}
                      type="button"
                      onClick={() => setNewKeyExpiry(opt.value)}
                      className={cn(
                        "px-3 py-1.5 rounded-md text-xs font-medium border transition-all",
                        newKeyExpiry === opt.value
                          ? "border-foreground/30 bg-muted"
                          : "border-border hover:border-muted-foreground/30"
                      )}
                    >
                      {opt.label}
                    </button>
                  ))}
                </div>
              </div>

              <DialogFooter>
                <Button
                  variant="outline"
                  onClick={() => setShowCreateDialog(false)}
                >
                  Cancel
                </Button>
                <Button
                  onClick={handleCreateKey}
                  disabled={isCreatingKey || !newKeyName.trim() || newKeyScopes.size === 0}
                >
                  {isCreatingKey ? (
                    <>
                      <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                      Creating...
                    </>
                  ) : (
                    "Create Key"
                  )}
                </Button>
              </DialogFooter>
            </div>
          )}
        </DialogContent>
      </Dialog>

      {/* Git Workspace */}
      <GitWorkspaceCard />
    </div>
  );
}

function GitWorkspaceCard() {
  const [repo, setRepo] = useState("");
  const [token, setToken] = useState("");
  const [branch, setBranch] = useState("main");
  const [isTesting, setIsTesting] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    // Load from server
    fetch(withBasePath("/api/vexa/user/workspace-git")).then(async (r) => {
      // GET doesn't exist — load from user profile data instead
    }).catch(() => {});
    // Also check localStorage as fallback
    try {
      const git = JSON.parse(localStorage.getItem("vexa-browser-git") || "{}");
      if (git.repo) {
        setRepo(git.repo);
        setToken(git.token || "");
        setBranch(git.branch || "main");
        setSaved(true);
      }
    } catch {}
  }, []);

  async function handleSave() {
    setIsSaving(true);
    try {
      const response = await fetch(withBasePath("/api/vexa/user/workspace-git"), {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ repo, token, branch }),
      });
      if (!response.ok) throw new Error(await response.text());
      // Also save to localStorage for the join modal to read
      localStorage.setItem("vexa-browser-git", JSON.stringify({ repo, token, branch }));
      setSaved(true);
      toast.success("Git workspace saved");
    } catch (error) {
      toast.error("Save failed: " + (error as Error).message);
    } finally {
      setIsSaving(false);
    }
  }

  async function handleClear() {
    try {
      await fetch(withBasePath("/api/vexa/user/workspace-git"), { method: "DELETE" });
      localStorage.removeItem("vexa-browser-git");
      setRepo("");
      setToken("");
      setBranch("main");
      setSaved(false);
      toast.success("Git workspace removed");
    } catch {
      toast.error("Failed to remove");
    }
  }

  async function handleTest() {
    setIsTesting(true);
    try {
      const repoPath = repo.replace("https://github.com/", "").replace(".git", "");
      const response = await fetch(`https://api.github.com/repos/${repoPath}`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      if (response.ok) {
        toast.success("Connected — repo accessible");
      } else if (response.status === 404) {
        toast.error("Repo not found — check URL and token permissions");
      } else {
        toast.error(`GitHub API error: ${response.status}`);
      }
    } catch (error) {
      toast.error("Connection failed: " + (error as Error).message);
    } finally {
      setIsTesting(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <GitBranch className="h-5 w-5" />
          Git Workspace
          {saved && repo && <Check className="h-4 w-4 text-green-500" />}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-sm text-muted-foreground">
          Connect a GitHub repo to sync browser session workspace files. Use a fine-grained PAT scoped to the repo only.
        </p>
        <div className="space-y-3">
          <div className="space-y-1">
            <Label className="text-xs">Repository URL</Label>
            <Input
              placeholder="https://github.com/you/workspace.git"
              value={repo}
              onChange={(e) => { setRepo(e.target.value); setSaved(false); }}
            />
          </div>
          {repo && (
            <>
              <div className="space-y-1">
                <Label className="text-xs">Personal Access Token</Label>
                <Input
                  placeholder="github_pat_..."
                  type="password"
                  value={token}
                  onChange={(e) => { setToken(e.target.value); setSaved(false); }}
                />
              </div>
              <div className="space-y-1">
                <Label className="text-xs">Branch</Label>
                <Input
                  placeholder="main"
                  value={branch}
                  onChange={(e) => { setBranch(e.target.value); setSaved(false); }}
                />
              </div>
            </>
          )}
        </div>
        <div className="flex gap-2">
          <Button size="sm" onClick={handleSave} disabled={!repo || isSaving}>
            {isSaving ? <Loader2 className="h-3 w-3 animate-spin mr-1" /> : null}
            Save
          </Button>
          {repo && token && (
            <Button size="sm" variant="outline" onClick={handleTest} disabled={isTesting}>
              {isTesting ? <Loader2 className="h-3 w-3 animate-spin mr-1" /> : <RefreshCw className="h-3 w-3 mr-1" />}
              Test
            </Button>
          )}
          {saved && repo && (
            <Button size="sm" variant="ghost" onClick={handleClear}>
              Remove
            </Button>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
