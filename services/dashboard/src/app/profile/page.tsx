"use client";

import { useState, useEffect } from "react";
import {
  User,
  Key,
  Copy,
  Loader2,
  Plus,
  Check,
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

// ==========================================
// Types
// ==========================================

interface APIKeyDisplay {
  id: string;
  name: string;
  scope: "bot" | "tx" | "user";
  token: string;
  masked_token: string;
  created_at: string;
}

type KeyScope = "bot" | "tx" | "user";

const SCOPE_CONFIG: Record<KeyScope, { label: string; prefix: string; color: string; bgColor: string }> = {
  bot: { label: "bot", prefix: "vxa_bot_", color: "text-purple-300", bgColor: "bg-purple-900/40" },
  tx: { label: "tx", prefix: "vxa_tx_", color: "text-cyan-300", bgColor: "bg-cyan-900/40" },
  user: { label: "user", prefix: "vxa_user_", color: "text-amber-300", bgColor: "bg-amber-900/40" },
};

// ==========================================
// Helpers
// ==========================================

function inferScope(token: string): KeyScope {
  if (token.startsWith("vxa_bot_")) return "bot";
  if (token.startsWith("vxa_tx_")) return "tx";
  if (token.startsWith("vxa_user_")) return "user";
  return "user";
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
  const [newKeyScope, setNewKeyScope] = useState<KeyScope>("bot");
  const [isCreatingKey, setIsCreatingKey] = useState(false);
  const [createdKeyToken, setCreatedKeyToken] = useState<string | null>(null);
  const [copiedKeyId, setCopiedKeyId] = useState<string | null>(null);


  // Fetch API keys
  useEffect(() => {
    async function fetchKeys() {
      if (!user?.id) return;
      try {
        const response = await fetch(`/api/profile/keys?userId=${user.id}`);
        if (!response.ok) {
          // Graceful fallback — endpoint may not exist yet
          setApiKeys([]);
          return;
        }
        const data = await response.json();
        setApiKeys(
          (data.keys || []).map((k: { id: string; token: string; name?: string; created_at: string }) => ({
            id: k.id,
            name: k.name || "API Key",
            scope: inferScope(k.token),
            token: k.token,
            masked_token: maskToken(k.token),
            created_at: k.created_at,
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
      const response = await fetch("/api/profile/keys", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: newKeyName, scope: newKeyScope, userId: user?.id }),
      });
      if (!response.ok) throw new Error("Failed to create key");
      const data = await response.json();
      setCreatedKeyToken(data.token);
      // Add to list
      setApiKeys((prev) => [
        ...prev,
        {
          id: data.id,
          name: newKeyName || "API Key",
          scope: newKeyScope,
          token: data.token,
          masked_token: maskToken(data.token),
          created_at: new Date().toISOString(),
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
      const response = await fetch(`/api/profile/keys/${keyId}`, { method: "DELETE" });
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
                  setNewKeyScope("bot");
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
                        <span
                          className={cn(
                            "inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold",
                            SCOPE_CONFIG[key.scope].bgColor,
                            SCOPE_CONFIG[key.scope].color
                          )}
                        >
                          {SCOPE_CONFIG[key.scope].label}
                        </span>
                      </div>
                      <p className="text-[11px] font-mono text-muted-foreground mt-0.5">
                        {key.masked_token}
                      </p>
                    </div>
                    <div className="flex items-center gap-3 text-xs">
                      <span className="text-muted-foreground">
                        {new Date(key.created_at).toLocaleDateString("en-US", {
                          month: "short",
                          day: "numeric",
                        })}
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
                <Label>Key Type</Label>
                <div className="grid grid-cols-2 gap-3">
                  <button
                    type="button"
                    onClick={() => setNewKeyScope("bot")}
                    className={cn(
                      "p-3 rounded-lg border-2 text-left transition-all",
                      newKeyScope === "bot"
                        ? "border-purple-500 bg-purple-950/20"
                        : "border-border hover:border-muted-foreground/30"
                    )}
                  >
                    <div className="flex items-center gap-2 mb-1">
                      <span className="text-sm font-medium">Bot Key</span>
                      <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold bg-purple-900/40 text-purple-300">
                        bot
                      </span>
                    </div>
                    <p className="text-[11px] text-muted-foreground">
                      Prefix: vxa_bot_ — Bot management & meetings
                    </p>
                  </button>
                  <button
                    type="button"
                    onClick={() => setNewKeyScope("tx")}
                    className={cn(
                      "p-3 rounded-lg border-2 text-left transition-all",
                      newKeyScope === "tx"
                        ? "border-cyan-500 bg-cyan-950/20"
                        : "border-border hover:border-muted-foreground/30"
                    )}
                  >
                    <div className="flex items-center gap-2 mb-1">
                      <span className="text-sm font-medium">TX Key</span>
                      <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold bg-cyan-900/40 text-cyan-300">
                        tx
                      </span>
                    </div>
                    <p className="text-[11px] text-muted-foreground">
                      Prefix: vxa_tx_ — Transcript access only
                    </p>
                  </button>
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
                  disabled={isCreatingKey || !newKeyName.trim()}
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
    </div>
  );
}
