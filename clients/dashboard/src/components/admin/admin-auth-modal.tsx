"use client";

import { useState, useEffect } from "react";
import { Shield, Eye, EyeOff, Loader2, AlertCircle, Lock } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { useAdminAuthStore } from "@/stores/admin-auth-store";

interface AdminAuthModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSuccess?: () => void;
}

export function AdminAuthModal({ open, onOpenChange, onSuccess }: AdminAuthModalProps) {
  const [token, setToken] = useState("");
  const [showToken, setShowToken] = useState(false);
  const { isVerifying, error, verifyAdminToken, clearError } = useAdminAuthStore();

  // Clear state when modal opens/closes
  useEffect(() => {
    if (open) {
      setToken("");
      setShowToken(false);
      clearError();
    }
  }, [open, clearError]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    if (!token.trim()) return;

    const success = await verifyAdminToken(token.trim());

    if (success) {
      onOpenChange(false);
      onSuccess?.();
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader className="space-y-4">
          <div className="mx-auto flex h-16 w-16 items-center justify-center rounded-full bg-gradient-to-br from-primary/20 to-primary/5 border border-primary/20">
            <Shield className="h-8 w-8 text-primary" />
          </div>
          <div className="text-center">
            <DialogTitle className="text-xl">Admin Authentication</DialogTitle>
            <DialogDescription className="mt-2">
              Enter your admin API token to access the administration panel
            </DialogDescription>
          </div>
        </DialogHeader>

        <form onSubmit={handleSubmit} className="space-y-6 pt-4">
          {error && (
            <Alert variant="destructive" className="animate-shake">
              <AlertCircle className="h-4 w-4" />
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}

          <div className="space-y-2">
            <Label htmlFor="admin-token" className="flex items-center gap-2">
              <Lock className="h-4 w-4 text-muted-foreground" />
              Admin Token
            </Label>
            <div className="relative">
              <Input
                id="admin-token"
                type={showToken ? "text" : "password"}
                placeholder="Enter your admin API token..."
                value={token}
                onChange={(e) => setToken(e.target.value)}
                className="pr-10 font-mono text-sm"
                autoComplete="off"
                autoFocus
              />
              <Button
                type="button"
                variant="ghost"
                size="icon"
                className="absolute right-0 top-0 h-full px-3 hover:bg-transparent"
                onClick={() => setShowToken(!showToken)}
              >
                {showToken ? (
                  <EyeOff className="h-4 w-4 text-muted-foreground" />
                ) : (
                  <Eye className="h-4 w-4 text-muted-foreground" />
                )}
              </Button>
            </div>
            <p className="text-xs text-muted-foreground">
              This is the VEXA_ADMIN_API_KEY configured in your environment
            </p>
          </div>

          <div className="flex gap-3">
            <Button
              type="button"
              variant="outline"
              className="flex-1"
              onClick={() => onOpenChange(false)}
              disabled={isVerifying}
            >
              Cancel
            </Button>
            <Button
              type="submit"
              className="flex-1"
              disabled={!token.trim() || isVerifying}
            >
              {isVerifying ? (
                <>
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  Verifying...
                </>
              ) : (
                <>
                  <Shield className="mr-2 h-4 w-4" />
                  Authenticate
                </>
              )}
            </Button>
          </div>
        </form>

        <div className="mt-4 pt-4 border-t">
          <p className="text-xs text-center text-muted-foreground">
            Admin access is required to manage users, tokens, and system settings.
            Your session will expire after 24 hours.
          </p>
        </div>
      </DialogContent>
    </Dialog>
  );
}
