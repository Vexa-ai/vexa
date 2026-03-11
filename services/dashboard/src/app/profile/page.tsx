"use client";

import { useState } from "react";
import { User, Key, Copy, Check, Eye, EyeOff, RefreshCw, Loader2 } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { Badge } from "@/components/ui/badge";
import { toast } from "sonner";
import { useAuthStore } from "@/stores/auth-store";

export default function ProfilePage() {
  const { user, token, logout } = useAuthStore();
  const [showToken, setShowToken] = useState(false);
  const [copied, setCopied] = useState(false);
  const [isRegenerating, setIsRegenerating] = useState(false);

  const handleCopyToken = async () => {
    if (!token) return;
    try {
      await navigator.clipboard.writeText(token);
      setCopied(true);
      toast.success("API key copied to clipboard");
      setTimeout(() => setCopied(false), 2000);
    } catch {
      toast.error("Failed to copy to clipboard");
    }
  };

  const handleRegenerateToken = async () => {
    if (!user?.email) return;
    setIsRegenerating(true);
    try {
      const response = await fetch("/api/auth/send-magic-link", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: user.email }),
      });
      const data = await response.json();
      if (data.mode === "direct" && data.token) {
        useAuthStore.getState().setAuth(data.user, data.token);
        toast.success("API key regenerated");
      } else {
        toast.error("Could not regenerate token");
      }
    } catch {
      toast.error("Failed to regenerate token");
    } finally {
      setIsRegenerating(false);
    }
  };

  const maskedToken = token
    ? `${token.substring(0, 12)}${"•".repeat(20)}${token.substring(token.length - 4)}`
    : "No token available";

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-[-0.02em] text-foreground">Profile</h1>
        <p className="text-sm text-muted-foreground">
          Your account details and API key
        </p>
      </div>

      <div className="max-w-2xl space-y-6">
        {/* Account Info */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <User className="h-5 w-5" />
              Account
            </CardTitle>
            <CardDescription>
              Your Vexa account information
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-2">
              <Label>Email</Label>
              <Input
                value={user?.email || "Not available"}
                disabled
                className="bg-muted"
              />
            </div>
            <div className="space-y-2">
              <Label>Name</Label>
              <Input
                value={user?.name || "Not set"}
                disabled
                className="bg-muted"
              />
            </div>
            {user?.max_concurrent_bots !== undefined && (
              <div className="space-y-2">
                <Label>Concurrent Bot Limit</Label>
                <div className="flex items-center gap-2">
                  <Input
                    value={String(user.max_concurrent_bots)}
                    disabled
                    className="bg-muted w-24"
                  />
                  <span className="text-sm text-muted-foreground">max active bots</span>
                </div>
              </div>
            )}
            {user?.created_at && (
              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                Member since {new Date(user.created_at).toLocaleDateString()}
              </div>
            )}
          </CardContent>
        </Card>

        {/* API Key */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Key className="h-5 w-5" />
              API Key
            </CardTitle>
            <CardDescription>
              Use this key to authenticate API requests. Include it in the <code className="bg-muted px-1 rounded text-xs">X-API-Key</code> header.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-2">
              <Label>Your API Key</Label>
              <div className="flex items-center gap-2">
                <div className="relative flex-1">
                  <Input
                    value={showToken ? (token || "") : maskedToken}
                    disabled
                    className="font-mono bg-muted pr-10 text-sm"
                  />
                </div>
                <Button
                  variant="outline"
                  size="icon"
                  onClick={() => setShowToken(!showToken)}
                  title={showToken ? "Hide" : "Show"}
                >
                  {showToken ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                </Button>
                <Button
                  variant="outline"
                  size="icon"
                  onClick={handleCopyToken}
                  title="Copy"
                  disabled={!token}
                >
                  {copied ? <Check className="h-4 w-4 text-green-500" /> : <Copy className="h-4 w-4" />}
                </Button>
              </div>
              {token && (
                <div className="flex items-center gap-2">
                  <Badge variant="secondary" className="text-xs">
                    {token.startsWith("vxa_") ? token.split("_").slice(0, 2).join("_") + "_..." : "legacy"}
                  </Badge>
                  <span className="text-xs text-muted-foreground">
                    {token.length} characters
                  </span>
                </div>
              )}
            </div>

            <Separator />

            <div className="flex items-center justify-between">
              <div className="space-y-1">
                <p className="text-sm font-medium">Regenerate Key</p>
                <p className="text-xs text-muted-foreground">
                  Creates a new API key. The old key will stop working.
                </p>
              </div>
              <Button
                variant="outline"
                size="sm"
                onClick={handleRegenerateToken}
                disabled={isRegenerating}
              >
                {isRegenerating ? (
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                ) : (
                  <RefreshCw className="mr-2 h-4 w-4" />
                )}
                Regenerate
              </Button>
            </div>

            <div className="rounded-lg bg-muted/50 border p-3">
              <p className="text-xs text-muted-foreground">
                <strong>Usage example:</strong>
              </p>
              <pre className="mt-2 text-xs font-mono overflow-x-auto">
{`curl -H "X-API-Key: ${token ? token.substring(0, 12) + "..." : "your_api_key"}" \\
  https://your-vexa-instance/api/meetings`}
              </pre>
            </div>
          </CardContent>
        </Card>

        {/* Danger Zone */}
        <Card className="border-destructive/20">
          <CardHeader>
            <CardTitle className="text-destructive">Danger Zone</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="flex items-center justify-between">
              <div className="space-y-1">
                <p className="text-sm font-medium">Sign Out</p>
                <p className="text-xs text-muted-foreground">
                  Sign out of your account on this device
                </p>
              </div>
              <Button variant="destructive" size="sm" onClick={logout}>
                Sign Out
              </Button>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
