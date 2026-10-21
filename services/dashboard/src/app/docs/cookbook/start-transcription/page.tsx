import { Metadata } from "next";
import Link from "next/link";
import { ArrowRight, CheckCircle } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export const metadata: Metadata = {
  title: "Start Transcription Bot | Vexa API Cookbook",
  description: "Step-by-step guide to sending a transcription bot to a meeting",
};

export default function StartTranscriptionPage() {
  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Start Transcription Bot</h1>
        <p className="text-muted-foreground mt-2">
          Learn how to send a transcription bot to a meeting and start receiving transcripts.
        </p>
      </div>

      <div className="prose prose-sm max-w-none space-y-6">
        <div>
          <h2>Overview</h2>
          <p>
            This cookbook walks you through the complete flow of starting a transcription bot:
          </p>
          <ol>
            <li>Create a bot to join the meeting</li>
            <li>Monitor bot status as it joins</li>
            <li>Receive live transcripts via WebSocket</li>
            <li>Fetch final transcript after meeting ends</li>
          </ol>
        </div>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <CheckCircle className="h-5 w-5 text-green-500" />
              Step 1: Create Bot
            </CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-sm mb-4">
              Send a POST request to create a bot. The bot will automatically join the meeting.
            </p>
            <div className="space-y-4">
              <div>
                <code className="bg-muted px-2 py-1 rounded text-sm font-mono">POST /bots</code>
              </div>
              <pre className="bg-muted p-4 rounded-lg text-sm overflow-x-auto">
                <code>{JSON.stringify({
                  platform: "google_meet",
                  native_meeting_id: "abc-defg-hij",
                  bot_name: "My Transcription Bot",
                  language: "en",
                }, null, 2)}</code>
              </pre>
              <Link href="/docs/rest/bots" className="text-sm text-primary hover:underline inline-flex items-center gap-1">
                View full API reference
                <ArrowRight className="h-4 w-4" />
              </Link>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <CheckCircle className="h-5 w-5 text-green-500" />
              Step 2: Monitor Status
            </CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-sm mb-4">
              The bot will transition through these statuses:
            </p>
            <ul className="text-sm space-y-2">
              <li><code className="bg-muted px-1 rounded">requested</code> - Bot creation requested</li>
              <li><code className="bg-muted px-1 rounded">joining</code> - Bot is connecting to the meeting</li>
              <li><code className="bg-muted px-1 rounded">awaiting_admission</code> - Bot is waiting in the lobby</li>
              <li><code className="bg-muted px-1 rounded">active</code> - Bot is in the meeting and transcribing</li>
              <li><code className="bg-muted px-1 rounded">completed</code> - Meeting ended, transcription complete</li>
            </ul>
            <p className="text-sm mt-4">
              Poll the meeting status or use WebSocket to receive real-time status updates.
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <CheckCircle className="h-5 w-5 text-green-500" />
              Step 3: Receive Live Transcripts
            </CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-sm mb-4">
              Connect to the WebSocket API to receive transcript segments in real-time.
            </p>
            <Link href="/docs/ws" className="text-sm text-primary hover:underline inline-flex items-center gap-1">
              Learn about WebSocket API
              <ArrowRight className="h-4 w-4" />
            </Link>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <CheckCircle className="h-5 w-5 text-green-500" />
              Step 4: Fetch Final Transcript
            </CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-sm mb-4">
              After the meeting ends, fetch the complete transcript via REST API.
            </p>
            <Link href="/docs/rest/transcripts" className="text-sm text-primary hover:underline inline-flex items-center gap-1">
              View Transcripts API
              <ArrowRight className="h-4 w-4" />
            </Link>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

