"use client";

import { useState, useEffect, useMemo } from "react";
import { useRouter } from "next/navigation";
import Image from "next/image";
import { signIn } from "next-auth/react";
import { Mail, Loader2, CheckCircle, ArrowLeft, AlertTriangle, XCircle, ArrowRight, Plus } from "lucide-react";
import { Logo } from "@/components/ui/logo";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { useAuthStore } from "@/stores/auth-store";
import { toast } from "sonner";
import { parseMeetingInput } from "@/lib/parse-meeting-input";
import { savePendingMeetingUrl } from "@/lib/pending-meeting";
import { cn } from "@/lib/utils";

type LoginState = "onboarding" | "email" | "sent";

interface HealthStatus {
  status: "ok" | "degraded" | "error";
  authMode: "direct" | "magic-link" | "google" | "oauth";
  checks: {
    smtp: { configured: boolean; optional?: boolean; error?: string };
    googleOAuth: { configured: boolean; optional?: boolean; error?: string };
    microsoftOAuth?: { configured: boolean; optional?: boolean; error?: string };
    adminApi: { configured: boolean; reachable: boolean; error?: string };
    vexaApi: { configured: boolean; reachable: boolean; error?: string };
  };
  missingConfig: string[];
}

export default function LoginPage() {
  const router = useRouter();
  const { sendMagicLink, isLoading, isAuthenticated } = useAuthStore();
  const [email, setEmail] = useState("");
  const [state, setState] = useState<LoginState>("onboarding");
  const [meetingInput, setMeetingInput] = useState("");
  const [healthStatus, setHealthStatus] = useState<HealthStatus | null>(null);
  const [healthLoading, setHealthLoading] = useState(true);

  const parsedInput = useMemo(() => parseMeetingInput(meetingInput), [meetingInput]);
  const isMeetingValid = parsedInput !== null;
  // Only allow Google Meet and Teams for now
  const isSupportedPlatform = parsedInput?.platform === "google_meet" || parsedInput?.platform === "teams";
  const canContinue = isMeetingValid && isSupportedPlatform;

  useEffect(() => {
    if (isAuthenticated) {
      router.push("/");
    }
  }, [isAuthenticated, router]);

  useEffect(() => {
    const checkHealth = async () => {
      try {
        const response = await fetch("/api/health");
        const data = await response.json();
        setHealthStatus(data);
      } catch {
        setHealthStatus({
          status: "error",
          authMode: "direct",
          checks: {
            smtp: { configured: false, optional: true, error: "Cannot reach server" },
            googleOAuth: { configured: false, optional: true, error: "Cannot reach server" },
            adminApi: { configured: false, reachable: false, error: "Cannot reach server" },
            vexaApi: { configured: false, reachable: false, error: "Cannot reach server" },
          },
          missingConfig: [],
        });
      } finally {
        setHealthLoading(false);
      }
    };

    checkHealth();
  }, []);

  const handleMeetingContinue = () => {
    if (!parsedInput) {
      toast.error("Please enter a valid meeting URL");
      return;
    }
    if (!isSupportedPlatform) {
      toast.error("Only Google Meet and Microsoft Teams are supported right now");
      return;
    }
    savePendingMeetingUrl(meetingInput);
    setState("email");
  };

  const handleMeetingKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && canContinue) {
      handleMeetingContinue();
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    if (!email) {
      toast.error("Please enter your email");
      return;
    }

    const result = await sendMagicLink(email);

    if (result.success) {
      if (result.mode === "direct") {
        toast.success(result.isNewUser ? "Account created! Welcome to Vexa." : "Welcome back!");
        router.push("/");
      } else {
        setState("sent");
        toast.success("Magic link sent! Check your email.");
      }
    } else {
      toast.error(result.error || "Failed to send magic link");
    }
  };

  const handleResend = async () => {
    const result = await sendMagicLink(email);

    if (result.success) {
      toast.success("Magic link sent again! Check your email.");
    } else {
      toast.error(result.error || "Failed to resend magic link");
    }
  };

  const handleBack = () => {
    setState("email");
  };

  const handleGoogleSignIn = async () => {
    try {
      await signIn("google", {
        callbackUrl: "/",
        redirect: true,
      });
    } catch (error) {
      console.error("Google sign-in error:", error);
      toast.error("Failed to sign in with Google");
    }
  };

  const handleMicrosoftSignIn = async () => {
    try {
      await signIn("microsoft", {
        callbackUrl: "/",
        redirect: true,
      });
    } catch (error) {
      console.error("Microsoft sign-in error:", error);
      toast.error("Failed to sign in with Microsoft");
    }
  };

  const isConfigError = healthStatus?.status === "error";
  const hasWarnings = healthStatus?.status === "degraded";
  const isDirectMode = healthStatus?.authMode === "direct";
  const isGoogleAuthEnabled = healthStatus?.checks.googleOAuth.configured === true;
  const isMicrosoftAuthEnabled = healthStatus?.checks.microsoftOAuth?.configured === true;
  const isOAuthEnabled = isGoogleAuthEnabled || isMicrosoftAuthEnabled;
  const isEmailAuthEnabled = !isOAuthEnabled && (healthStatus?.authMode === "magic-link" || healthStatus?.authMode === "direct");

  // Landing page onboarding state
  if (state === "onboarding") {
    return (
      <div className="min-h-screen flex flex-col items-center justify-center bg-background px-4">
        {/* Large Vexa wordmark */}
        <div className="mb-12 flex flex-col items-center gap-3">
          <Logo size="lg" showText={false} />
          <span className="text-lg font-semibold tracking-[-0.02em] text-foreground">vexa</span>
        </div>

        {/* Hero heading */}
        <h1 className="text-3xl sm:text-4xl md:text-5xl font-semibold tracking-[-0.03em] text-foreground text-center max-w-2xl leading-[1.1]">
          Drop a bot to your meeting
        </h1>

        {/* Input area */}
        <div className="w-full max-w-xl mt-10">
          <div className={cn(
            "relative flex items-center rounded-2xl border bg-card shadow-[0_1px_3px_rgba(0,0,0,0.04),0_8px_32px_-8px_rgba(0,0,0,0.06)] transition-all",
            meetingInput && canContinue
              ? "border-border ring-1 ring-primary/20"
              : meetingInput && isMeetingValid && !isSupportedPlatform
              ? "border-orange-300"
              : "border-border"
          )}>
            {/* Platform icon inside input */}
            {parsedInput && isSupportedPlatform && (
              <div className="absolute left-4 top-1/2 -translate-y-1/2 z-10">
                <Image
                  src={parsedInput.platform === "google_meet"
                    ? "/icons/icons8-google-meet-96.png"
                    : "/icons/icons8-teams-96.png"
                  }
                  alt={parsedInput.platform === "google_meet" ? "Google Meet" : "Microsoft Teams"}
                  width={24}
                  height={24}
                  className="rounded"
                />
              </div>
            )}
            <input
              type="text"
              placeholder="Paste meeting URL..."
              value={meetingInput}
              onChange={(e) => setMeetingInput(e.target.value)}
              onKeyDown={handleMeetingKeyDown}
              className={cn(
                "flex-1 bg-transparent px-5 py-4 text-base text-foreground placeholder:text-muted-foreground focus:outline-none",
                parsedInput && isSupportedPlatform && "pl-12"
              )}
              autoFocus
              autoComplete="off"
            />
            {/* Submit arrow button */}
            <button
              onClick={handleMeetingContinue}
              disabled={!canContinue}
              className={cn(
                "mr-3 flex h-9 w-9 items-center justify-center rounded-xl transition-all",
                canContinue
                  ? "bg-foreground text-background hover:opacity-80 cursor-pointer"
                  : "bg-muted text-muted-foreground cursor-not-allowed"
              )}
            >
              <ArrowRight className="h-4 w-4" />
            </button>
          </div>

          {/* Unsupported platform hint */}
          {meetingInput && isMeetingValid && !isSupportedPlatform && (
            <p className="mt-2 text-sm text-orange-600 dark:text-orange-400 text-center">
              Only Google Meet and Microsoft Teams are supported right now
            </p>
          )}
        </div>

        {/* Platform chips */}
        <div className="flex flex-wrap items-center justify-center gap-3 mt-8">
          <div className="flex items-center gap-2.5 px-4 py-2.5 rounded-full border border-border bg-card text-sm text-muted-foreground">
            <Image
              src="/icons/icons8-google-meet-96.png"
              alt="Google Meet"
              width={20}
              height={20}
              className="rounded-sm"
            />
            Google Meet
          </div>
          <div className="flex items-center gap-2.5 px-4 py-2.5 rounded-full border border-border bg-card text-sm text-muted-foreground">
            <Image
              src="/icons/icons8-teams-96.png"
              alt="Microsoft Teams"
              width={20}
              height={20}
              className="rounded-sm"
            />
            Microsoft Teams
          </div>
          <a
            href="https://meet.new"
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-2 px-4 py-2.5 rounded-full border border-dashed border-border bg-card text-sm text-muted-foreground hover:text-foreground hover:border-gray-400 transition-colors"
          >
            <Plus className="h-4 w-4" />
            Create a Meet
          </a>
        </div>

        {/* Sign in link */}
        <button
          type="button"
          onClick={() => setState("email")}
          className="mt-10 text-sm text-muted-foreground hover:text-foreground transition-colors"
        >
          Already have an account? Sign in
        </button>

        <p className="absolute bottom-6 text-[11.5px] text-muted-foreground">
          Open Source · Developer-first · API-first
        </p>
      </div>
    );
  }

  // Auth states (email / sent) — also landing-page style
  return (
    <div className="min-h-screen flex flex-col items-center justify-center bg-background px-4">
      {/* Logo */}
      <div className="mb-10 flex flex-col items-center gap-3">
        <Logo size="lg" showText={false} />
        <span className="text-lg font-semibold tracking-[-0.02em] text-foreground">vexa</span>
      </div>

      {/* Configuration Error Banner */}
      {!healthLoading && isConfigError && (
        <div className="w-full max-w-md mb-4 p-4 rounded-lg bg-destructive/10 border border-destructive/20">
          <div className="flex items-start gap-3">
            <XCircle className="h-5 w-5 text-destructive mt-0.5 flex-shrink-0" />
            <div className="flex-1">
              <h3 className="font-medium text-destructive">Server Configuration Error</h3>
              <p className="text-sm text-muted-foreground mt-1">
                The server is not properly configured. Please contact the administrator.
              </p>
              {healthStatus?.checks.adminApi.error && (
                <p className="text-xs text-muted-foreground mt-1">
                  {healthStatus.checks.adminApi.error}
                </p>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Warning Banner */}
      {!healthLoading && hasWarnings && (
        <div className="w-full max-w-md mb-4 p-4 rounded-lg bg-yellow-500/10 border border-yellow-500/20">
          <div className="flex items-start gap-3">
            <AlertTriangle className="h-5 w-5 text-yellow-600 dark:text-yellow-500 mt-0.5 flex-shrink-0" />
            <div className="flex-1">
              <h3 className="font-medium text-yellow-600 dark:text-yellow-500">Connection Warning</h3>
              <p className="text-sm text-muted-foreground mt-1">
                Some services may be unavailable.
              </p>
            </div>
          </div>
        </div>
      )}

      {state === "email" ? (
        <div className="w-full max-w-sm">
          <h2 className="text-2xl font-semibold tracking-[-0.02em] text-foreground text-center mb-2">
            Sign in to continue
          </h2>
          <p className="text-sm text-muted-foreground text-center mb-8">
            Choose your provider to get started
          </p>

          <div className="space-y-3">
            {/* Google Auth */}
            <button
              onClick={handleGoogleSignIn}
              disabled={isConfigError}
              className="w-full flex items-center gap-3 px-4 py-3.5 rounded-xl border border-border bg-card hover:bg-accent hover:border-gray-300 transition-all text-left disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <svg className="h-5 w-5 flex-shrink-0" viewBox="0 0 24 24">
                <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" />
                <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" />
                <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" />
                <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" />
              </svg>
              <div className="flex-1">
                <span className="text-sm font-medium text-foreground">Continue with Google</span>
              </div>
              <ArrowRight className="h-4 w-4 text-muted-foreground" />
            </button>

            {/* Microsoft Auth */}
            <button
              onClick={handleMicrosoftSignIn}
              disabled={isConfigError}
              className="w-full flex items-center gap-3 px-4 py-3.5 rounded-xl border border-border bg-card hover:bg-accent hover:border-gray-300 transition-all text-left disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <svg className="h-5 w-5 flex-shrink-0" viewBox="0 0 21 21">
                <rect x="1" y="1" width="9" height="9" fill="#F25022" />
                <rect x="11" y="1" width="9" height="9" fill="#7FBA00" />
                <rect x="1" y="11" width="9" height="9" fill="#00A4EF" />
                <rect x="11" y="11" width="9" height="9" fill="#FFB900" />
              </svg>
              <div className="flex-1">
                <span className="text-sm font-medium text-foreground">Continue with Microsoft</span>
              </div>
              <ArrowRight className="h-4 w-4 text-muted-foreground" />
            </button>
          </div>

          {/* Email fallback */}
          {isEmailAuthEnabled && (
            <>
              <div className="relative my-6">
                <div className="absolute inset-0 flex items-center">
                  <Separator />
                </div>
                <div className="relative flex justify-center text-xs uppercase">
                  <span className="bg-background px-2 text-muted-foreground">Or</span>
                </div>
              </div>

              <form onSubmit={handleSubmit} className="space-y-3">
                <div className="relative">
                  <Mail className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                  <Input
                    id="email"
                    type="email"
                    placeholder="you@example.com"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    className="pl-10"
                    disabled={isLoading || isConfigError}
                  />
                </div>

                <Button
                  type="submit"
                  className="w-full"
                  variant="outline"
                  disabled={isLoading || healthLoading || isConfigError}
                >
                  {healthLoading ? (
                    <>
                      <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                      Checking server...
                    </>
                  ) : isLoading ? (
                    <>
                      <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                      {isDirectMode ? "Signing in..." : "Sending link..."}
                    </>
                  ) : isConfigError ? (
                    "Server Unavailable"
                  ) : isDirectMode ? (
                    "Continue with Email"
                  ) : (
                    "Continue with Email"
                  )}
                </Button>
              </form>
            </>
          )}

          <div className="mt-6 text-center">
            <button
              type="button"
              onClick={() => setState("onboarding")}
              className="text-sm text-muted-foreground hover:text-foreground transition-colors"
            >
              <ArrowLeft className="inline mr-1 h-3 w-3" />
              Back
            </button>
          </div>
        </div>
      ) : (
        <div className="w-full max-w-sm">
          <div className="flex justify-center mb-6">
            <div className="h-16 w-16 rounded-full bg-green-100 dark:bg-green-900/30 flex items-center justify-center">
              <CheckCircle className="h-8 w-8 text-green-600 dark:text-green-400" />
            </div>
          </div>
          <h2 className="text-2xl font-semibold tracking-[-0.02em] text-foreground text-center mb-2">
            Check your email
          </h2>
          <p className="text-sm text-muted-foreground text-center mb-8">
            We sent a magic link to <span className="font-medium text-foreground">{email}</span>
          </p>

          <div className="bg-muted/50 rounded-lg p-4 text-sm text-muted-foreground mb-4">
            <p className="mb-2">Click the link in the email to sign in.</p>
            <p>The link will expire in 15 minutes.</p>
          </div>

          <div className="flex flex-col gap-2">
            <Button
              variant="outline"
              onClick={handleResend}
              disabled={isLoading}
              className="w-full"
            >
              {isLoading ? (
                <>
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  Sending...
                </>
              ) : (
                "Resend magic link"
              )}
            </Button>

            <Button
              variant="ghost"
              onClick={handleBack}
              className="w-full"
            >
              <ArrowLeft className="mr-2 h-4 w-4" />
              Use a different email
            </Button>
          </div>
        </div>
      )}

      <p className="absolute bottom-6 text-[11.5px] text-muted-foreground">
        Open Source · Developer-first · API-first
      </p>
    </div>
  );
}
