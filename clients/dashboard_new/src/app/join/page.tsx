"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { JoinForm } from "@vexa/dash-join-form";
import { useVexa } from "../providers";

export default function JoinPage() {
  const { apiClient, config } = useVexa();
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  return (
    <div className="panel">
      <h2>Start a bot</h2>
      {error && <p style={{ color: "var(--bad)" }}>{error}</p>}
      <JoinForm
        defaultBotName={config?.defaultBotName || "Vexa"}
        onSubmit={async (request) => {
          setError(null);
          setSubmitting(true);
          try {
            // The form's CreateBotRequest and the api client's BotRequest are independent projections
            // of the same api.v1 POST /bots body — the composition root maps one onto the other.
            const meeting = await apiClient.postBot({
              platform: request.platform,
              native_meeting_id: request.native_meeting_id,
              bot_name: request.bot_name,
              meeting_url: request.meeting_url,
              passcode: request.passcode,
            });
            router.push(`/meetings/${meeting.id}`);
          } catch (e: unknown) {
            setError(e instanceof Error ? e.message : String(e));
            setSubmitting(false);
          }
        }}
      />
      {submitting && <p className="muted">Requesting bot…</p>}
    </div>
  );
}
