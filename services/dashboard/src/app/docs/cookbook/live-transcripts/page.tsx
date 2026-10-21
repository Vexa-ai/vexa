import { Metadata } from "next";
import Link from "next/link";
import { ArrowRight } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export const metadata: Metadata = {
  title: "Live Transcripts | Vexa API Cookbook",
  description: "How to receive real-time transcript updates via WebSocket",
};

export default function LiveTranscriptsPage() {
  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Live Transcripts</h1>
        <p className="text-muted-foreground mt-2">
          Learn how to receive real-time transcript updates as meetings progress.
        </p>
      </div>

      <div className="prose prose-sm max-w-none space-y-6">
        <div>
          <h2>Overview</h2>
          <p>
            To receive live transcripts, you need to:
          </p>
          <ol>
            <li>Connect to the WebSocket endpoint</li>
            <li>Subscribe to the meeting</li>
            <li>Handle transcript events as they arrive</li>
            <li>Process mutable and finalized segments</li>
          </ol>
        </div>

        <Card>
          <CardHeader>
            <CardTitle>Step 1: Connect and Subscribe</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-sm mb-4">
              First, connect to the WebSocket and subscribe to your meeting.
            </p>
            <Link href="/docs/ws/subscribe" className="text-sm text-primary hover:underline inline-flex items-center gap-1">
              Learn about subscribing
              <ArrowRight className="h-4 w-4" />
            </Link>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Step 2: Handle Transcript Events</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-sm mb-4">
              You'll receive two types of transcript events:
            </p>
            <ul className="text-sm space-y-2 mb-4">
              <li>
                <code className="bg-muted px-1 rounded">transcript.mutable</code> - Live segments that may be updated
              </li>
              <li>
                <code className="bg-muted px-1 rounded">transcript.finalized</code> - Final segments that won't change
              </li>
            </ul>
            <Link href="/docs/ws/events" className="text-sm text-primary hover:underline inline-flex items-center gap-1">
              View event reference
              <ArrowRight className="h-4 w-4" />
            </Link>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Step 3: Deduplication</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-sm mb-4">
              Since mutable segments can be updated, use <code className="bg-muted px-1 rounded">absolute_start_time</code> as a unique identifier
              and <code className="bg-muted px-1 rounded">updated_at</code> to determine which version is newer.
            </p>
            <p className="text-sm text-muted-foreground">
              Keep the segment with the latest <code className="bg-muted px-1 rounded">updated_at</code> timestamp.
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Step 4: Bootstrap from REST</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-sm mb-4">
              For best results, fetch existing transcripts via REST API first, then connect to WebSocket for live updates.
              This ensures you have all historical segments before receiving new ones.
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

