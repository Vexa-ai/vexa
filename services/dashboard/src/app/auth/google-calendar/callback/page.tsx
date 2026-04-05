"use client";

import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Loader2, CheckCircle2, XCircle } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Logo } from "@/components/ui/logo";
import { withBasePath } from "@/lib/base-path";

type CallbackState = "loading" | "success" | "error";

function CalendarCallbackContent() {
  const router = useRouter();
  const searchParams = useSearchParams();

  const [state, setState] = useState<CallbackState>("loading");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;

    async function run() {
      const code = searchParams.get("code");
      const stateParam = searchParams.get("state");
      const oauthError = searchParams.get("error");

      if (oauthError) {
        if (!mounted) return;
        setState("error");
        setError(
          oauthError === "access_denied"
            ? "Google Calendar authorization was cancelled or denied."
            : `Google Calendar authorization failed: ${oauthError}`
        );
        return;
      }

      if (!code || !stateParam) {
        if (!mounted) return;
        setState("error");
        setError("Missing OAuth callback parameters");
        return;
      }

      const completeResp = await fetch(withBasePath("/api/calendar/oauth/complete"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code, state: stateParam }),
      });

      const completeData = await completeResp.json();
      if (!completeResp.ok) {
        if (!mounted) return;
        setState("error");
        setError(completeData?.error || "Failed to complete Google Calendar OAuth");
        return;
      }

      if (!mounted) return;
      setState("success");
      setTimeout(() => {
        router.replace(completeData?.returnTo || "/meetings");
      }, 900);
    }

    run().catch((err) => {
      if (!mounted) return;
      setState("error");
      setError((err as Error).message || "Unexpected error during callback");
    });

    return () => {
      mounted = false;
    };
  }, [router, searchParams]);

  return (
    <Card className="border-0 shadow-xl">
      <CardHeader className="text-center">
        {state === "loading" && (
          <>
            <CardTitle className="text-xl">Connecting Google Calendar...</CardTitle>
            <CardDescription>Finalizing your authorization</CardDescription>
          </>
        )}

        {state === "success" && (
          <>
            <div className="flex justify-center mb-4">
              <div className="h-16 w-16 rounded-full bg-green-100 dark:bg-green-900/30 flex items-center justify-center">
                <CheckCircle2 className="h-8 w-8 text-green-600 dark:text-green-400" />
              </div>
            </div>
            <CardTitle className="text-xl text-green-600 dark:text-green-400">Calendar Connected</CardTitle>
            <CardDescription>Redirecting...</CardDescription>
          </>
        )}

        {state === "error" && (
          <>
            <div className="flex justify-center mb-4">
              <div className="h-16 w-16 rounded-full bg-destructive/10 flex items-center justify-center">
                <XCircle className="h-8 w-8 text-destructive" />
              </div>
            </div>
            <CardTitle className="text-xl text-destructive">Calendar Connection Failed</CardTitle>
            <CardDescription>{error || "Unknown error"}</CardDescription>
          </>
        )}
      </CardHeader>
      <CardContent className="flex flex-col items-center gap-4">
        {state === "loading" && (
          <Loader2 className="h-10 w-10 animate-spin text-primary" />
        )}
        {state === "error" && (
          <Button onClick={() => router.replace("/meetings")} className="w-full">
            Back to Meetings
          </Button>
        )}
      </CardContent>
    </Card>
  );
}

function CalendarCallbackLoading() {
  return (
    <Card className="border-0 shadow-xl">
      <CardHeader className="text-center">
        <CardTitle className="text-xl">Loading...</CardTitle>
        <CardDescription>Please wait</CardDescription>
      </CardHeader>
      <CardContent className="flex justify-center">
        <Loader2 className="h-10 w-10 animate-spin text-primary" />
      </CardContent>
    </Card>
  );
}

export default function GoogleCalendarCallbackPage() {
  return (
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-background to-muted/30 p-4">
      <div className="w-full max-w-md">
        <div className="flex flex-col items-center justify-center gap-2 mb-8">
          <Logo size="lg" showText />
          <p className="text-sm text-muted-foreground">Meeting Transcription</p>
        </div>
        <Suspense fallback={<CalendarCallbackLoading />}>
          <CalendarCallbackContent />
        </Suspense>
      </div>
    </div>
  );
}
