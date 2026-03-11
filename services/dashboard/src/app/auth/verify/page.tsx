"use client";

import { Suspense, useEffect, useState, useCallback } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Loader2, CheckCircle, XCircle, RefreshCw, AlertTriangle, Clock, WifiOff, ShieldX } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Logo } from "@/components/ui/logo";
import { useAuthStore } from "@/stores/auth-store";

type VerifyState = "verifying" | "success" | "error";

interface VerifyError {
  message: string;
  code?: string;
  details?: string;
  canRetry?: boolean;
}

// Map error codes to icons and colors
const errorConfig: Record<string, { icon: React.ElementType; color: string; title: string }> = {
  TOKEN_EXPIRED: { icon: Clock, color: "text-yellow-500", title: "Link Expired" },
  INVALID_TOKEN: { icon: ShieldX, color: "text-destructive", title: "Invalid Link" },
  NOT_CONFIGURED: { icon: AlertTriangle, color: "text-yellow-500", title: "Configuration Error" },
  TIMEOUT: { icon: WifiOff, color: "text-orange-500", title: "Connection Timeout" },
  NETWORK_ERROR: { icon: WifiOff, color: "text-orange-500", title: "Network Error" },
  SERVICE_UNAVAILABLE: { icon: WifiOff, color: "text-orange-500", title: "Service Unavailable" },
  UNAUTHORIZED: { icon: ShieldX, color: "text-destructive", title: "Access Denied" },
  default: { icon: XCircle, color: "text-destructive", title: "Verification Failed" },
};

function VerifyContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const token = searchParams.get("token");
  const { setAuth } = useAuthStore();

  const [state, setState] = useState<VerifyState>("verifying");
  const [error, setError] = useState<VerifyError | null>(null);
  const [isRetrying, setIsRetrying] = useState(false);
  const [isNewUser, setIsNewUser] = useState(false);

  const verifyToken = useCallback(async (isRetry = false) => {
    if (!token) {
      setState("error");
      setError({
        message: "No verification token provided",
        code: "MISSING_TOKEN",
      });
      return;
    }

    if (isRetry) {
      setIsRetrying(true);
    } else {
      setState("verifying");
    }

    try {
      const response = await fetch("/api/auth/verify", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ token }),
      });

      const data = await response.json();

      if (!response.ok) {
        setState("error");
        setError({
          message: data.error || "Verification failed",
          code: data.code,
          details: data.details,
          canRetry: data.canRetry,
        });
        setIsRetrying(false);
        return;
      }

      // Save to auth store (which saves to localStorage)
      setAuth(data.user, data.token);
      setIsNewUser(data.isNewUser);
      setState("success");

      // Redirect to home after short delay
      setTimeout(() => {
        router.push("/");
      }, 2500);
    } catch {
      setState("error");
      setError({
        message: "Connection error. Please check your internet connection.",
        code: "NETWORK_ERROR",
        canRetry: true,
      });
      setIsRetrying(false);
    }
  }, [token, router, setAuth]);

  useEffect(() => {
    verifyToken();
  }, [verifyToken]);

  const handleRetry = () => {
    verifyToken(true);
  };

  const errorInfo = error?.code ? (errorConfig[error.code] || errorConfig.default) : errorConfig.default;
  const ErrorIcon = errorInfo.icon;

  return (
    <>
      <Card className="border-0 shadow-xl">
        <CardHeader className="text-center">
          {state === "verifying" && (
            <>
              <CardTitle className="text-xl">
                {isRetrying ? "Retrying verification..." : "Verifying your email..."}
              </CardTitle>
              <CardDescription>
                Please wait while we verify your login link
              </CardDescription>
            </>
          )}

          {state === "success" && (
            <>
              <div className="flex justify-center mb-4">
                <div className="h-16 w-16 rounded-full bg-green-100 dark:bg-green-900/30 flex items-center justify-center">
                  <CheckCircle className="h-8 w-8 text-green-600 dark:text-green-400" />
                </div>
              </div>
              <CardTitle className="text-xl text-green-600 dark:text-green-400">
                {isNewUser ? "Account Created!" : "Welcome Back!"}
              </CardTitle>
              <CardDescription>
                {isNewUser
                  ? "Your account has been created. Redirecting..."
                  : "You have been signed in. Redirecting..."}
              </CardDescription>
            </>
          )}

          {state === "error" && error && (
            <>
              <div className="flex justify-center mb-4">
                <div className={`h-16 w-16 rounded-full bg-muted flex items-center justify-center`}>
                  <ErrorIcon className={`h-8 w-8 ${errorInfo.color}`} />
                </div>
              </div>
              <CardTitle className={`text-xl ${errorInfo.color}`}>
                {errorInfo.title}
              </CardTitle>
              <CardDescription className="mt-2">
                {error.message}
              </CardDescription>
            </>
          )}
        </CardHeader>

        <CardContent className="flex flex-col items-center gap-4">
          {state === "verifying" && (
            <Loader2 className="h-12 w-12 animate-spin text-primary" />
          )}

          {state === "success" && (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              <span>Redirecting to dashboard...</span>
            </div>
          )}

          {state === "error" && error && (
            <div className="w-full space-y-4">
              {/* Error code badge */}
              {error.code && (
                <div className="flex justify-center">
                  <span className="px-2 py-1 text-xs font-mono bg-muted rounded">
                    {error.code}
                  </span>
                </div>
              )}

              {/* Technical details (collapsible) */}
              {error.details && (
                <details className="text-xs text-muted-foreground bg-muted/50 rounded-lg p-3">
                  <summary className="cursor-pointer hover:text-foreground transition-colors">
                    Technical details
                  </summary>
                  <pre className="mt-2 whitespace-pre-wrap break-all font-mono">
                    {error.details}
                  </pre>
                </details>
              )}

              {/* Action buttons */}
              <div className="flex flex-col gap-2 pt-2">
                {error.canRetry && (
                  <Button
                    onClick={handleRetry}
                    disabled={isRetrying}
                    className="w-full"
                  >
                    {isRetrying ? (
                      <>
                        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                        Retrying...
                      </>
                    ) : (
                      <>
                        <RefreshCw className="mr-2 h-4 w-4" />
                        Try Again
                      </>
                    )}
                  </Button>
                )}

                <Button
                  variant={error.canRetry ? "outline" : "default"}
                  onClick={() => router.push("/login")}
                  className="w-full"
                >
                  {error.code === "TOKEN_EXPIRED" ? "Request New Link" : "Back to Login"}
                </Button>
              </div>
            </div>
          )}
        </CardContent>
      </Card>
    </>
  );
}

function VerifyLoading() {
  return (
    <Card className="border-0 shadow-xl">
      <CardHeader className="text-center">
        <CardTitle className="text-xl">Loading...</CardTitle>
        <CardDescription>Please wait</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col items-center gap-4">
        <Loader2 className="h-12 w-12 animate-spin text-primary" />
      </CardContent>
    </Card>
  );
}

export default function VerifyPage() {
  return (
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-background to-muted/30 p-4">
      <div className="w-full max-w-md">
        {/* Logo */}
        <div className="flex flex-col items-center justify-center gap-2 mb-8">
          <Logo size="lg" showText={true} />
          <p className="text-sm text-muted-foreground">Meeting Transcription</p>
        </div>

        <Suspense fallback={<VerifyLoading />}>
          <VerifyContent />
        </Suspense>

        <p className="text-center text-xs text-muted-foreground mt-6">
          Vexa Dashboard - Open Source Meeting Transcription
        </p>
      </div>
    </div>
  );
}
