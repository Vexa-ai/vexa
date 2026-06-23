"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { JoinForm } from "@vexa/dash-join-form";
import { toast } from "sonner";
import { useVexa } from "../providers";
import { Card, CardContent } from "@/components/ui/card";

export default function JoinPage() {
  const { apiClient, config } = useVexa();
  const router = useRouter();
  const [submitting, setSubmitting] = useState(false);

  return (
    <div className="mx-auto max-w-xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Start a bot</h1>
        <p className="text-muted-foreground mt-1 text-sm">
          Send a Vexa bot into a Google Meet, Teams, or Zoom meeting to transcribe and record it.
        </p>
      </div>

      <Card>
        <CardContent className="pt-6">
          <JoinForm
            defaultBotName={config?.defaultBotName || "Vexa"}
            onSubmit={async (request) => {
              setSubmitting(true);
              try {
                // The form's CreateBotRequest and the api client's BotRequest are independent
                // projections of the same api.v1 POST /bots body — map one onto the other (incl. the
                // DF1 knobs language/task/recording/transcription).
                const meeting = await apiClient.postBot({
                  platform: request.platform,
                  native_meeting_id: request.native_meeting_id,
                  bot_name: request.bot_name,
                  meeting_url: request.meeting_url,
                  passcode: request.passcode,
                  language: request.language,
                  task: request.task,
                  recording_enabled: request.recording_enabled,
                  transcribe_enabled: request.transcribe_enabled,
                });
                toast.success("Bot requested — joining the meeting");
                router.push(`/meetings/${meeting.id}`);
              } catch (e: unknown) {
                setSubmitting(false);
                toast.error(e instanceof Error ? e.message : String(e));
              }
            }}
          />
          {submitting && <p className="text-muted-foreground mt-3 text-sm">Requesting bot…</p>}
        </CardContent>
      </Card>
    </div>
  );
}
