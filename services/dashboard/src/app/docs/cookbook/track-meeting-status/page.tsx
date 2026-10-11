import { Metadata } from "next";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import Link from "next/link";
import { ExternalLink } from "lucide-react";

export const metadata: Metadata = {
  title: "Track Meeting Status | Vexa API Cookbook",
  description: "Learn how to track meeting status updates in real-time using WebSocket",
};

export default function TrackMeetingStatusPage() {
  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Track Meeting Status</h1>
        <p className="text-muted-foreground mt-2">
          Learn how to receive real-time meeting status updates via WebSocket to track bot progress from creation to completion.
        </p>
      </div>

      {/* Overview */}
      <Card>
        <CardHeader>
          <CardTitle>Overview</CardTitle>
          <CardDescription>
            The WebSocket API sends <code>meeting.status</code> events whenever a meeting's status changes. This allows you to build real-time status indicators and track bot progress.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="space-y-4">
            <div>
              <h3 className="text-sm font-semibold mb-2">Status Flow</h3>
              <div className="flex items-center gap-2 text-sm">
                <span className="px-2 py-1 bg-blue-100 dark:bg-blue-900 rounded">requested</span>
                <span>→</span>
                <span className="px-2 py-1 bg-blue-100 dark:bg-blue-900 rounded">joining</span>
                <span>→</span>
                <span className="px-2 py-1 bg-orange-100 dark:bg-orange-900 rounded">awaiting_admission</span>
                <span>→</span>
                <span className="px-2 py-1 bg-green-100 dark:bg-green-900 rounded">active</span>
                <span>→</span>
                <span className="px-2 py-1 bg-gray-100 dark:bg-gray-800 rounded">completed</span>
              </div>
            </div>
            <div>
              <p className="text-sm text-muted-foreground">
                The <code className="bg-muted px-1 rounded">failed</code> status can occur at any point if an error happens.
              </p>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Implementation */}
      <Card>
        <CardHeader>
          <CardTitle>Implementation</CardTitle>
          <CardDescription>
            Subscribe to WebSocket events and handle <code>meeting.status</code> messages
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Tabs defaultValue="js">
            <TabsList>
              <TabsTrigger value="js">JavaScript</TabsTrigger>
              <TabsTrigger value="python">Python</TabsTrigger>
              <TabsTrigger value="curl">curl</TabsTrigger>
            </TabsList>

            <TabsContent value="js" className="mt-4">
              <div className="space-y-2">
                <code className="text-xs text-muted-foreground">JavaScript/TypeScript Example</code>
                <pre className="bg-muted p-4 rounded-lg text-sm overflow-x-auto">
                  <code>{`// Connect to WebSocket
const ws = new WebSocket('wss://your-api-url/ws?api_key=your-api-key');

ws.onopen = () => {
  // Subscribe to meeting
  ws.send(JSON.stringify({
    action: 'subscribe',
    meetings: [
      { platform: 'google_meet', native_id: 'abc-defg-hij' }
    ]
  }));
};

ws.onmessage = (event) => {
  const message = JSON.parse(event.data);
  
  if (message.type === 'meeting.status') {
    const status = message.payload.status;
    const meeting = message.meeting;
    
    console.log(\`Meeting \${meeting.native_id} status: \${status}\`);
    
    // Handle different statuses
    switch (status) {
      case 'requested':
        console.log('Bot is starting up...');
        break;
      case 'joining':
        console.log('Bot is connecting to meeting...');
        break;
      case 'awaiting_admission':
        console.log('Bot is waiting in the waiting room');
        console.log('Please admit the bot to the meeting');
        break;
      case 'active':
        console.log('Bot is actively transcribing!');
        // Now you'll receive transcript events
        break;
      case 'completed':
        console.log('Transcription completed');
        // Disconnect or handle completion
        ws.close();
        break;
      case 'failed':
        console.error('Bot failed:', message.payload.error);
        ws.close();
        break;
    }
    
    // Update your UI with the new status
    updateStatusIndicator(meeting.native_id, status);
  }
};`}</code>
                </pre>
              </div>
            </TabsContent>

            <TabsContent value="python" className="mt-4">
              <div className="space-y-2">
                <code className="text-xs text-muted-foreground">Python Example</code>
                <pre className="bg-muted p-4 rounded-lg text-sm overflow-x-auto">
                  <code>{`import asyncio
import websockets
import json

async def track_meeting_status():
    uri = "wss://your-api-url/ws?api_key=your-api-key"
    
    async with websockets.connect(uri) as websocket:
        # Subscribe to meeting
        await websocket.send(json.dumps({
            "action": "subscribe",
            "meetings": [
                {"platform": "google_meet", "native_id": "abc-defg-hij"}
            ]
        }))
        
        # Listen for messages
        async for message in websocket:
            data = json.loads(message)
            
            if data.get("type") == "meeting.status":
                status = data["payload"]["status"]
                meeting = data["meeting"]
                
                print(f"Meeting {meeting['native_id']} status: {status}")
                
                # Handle different statuses
                if status == "requested":
                    print("Bot is starting up...")
                elif status == "joining":
                    print("Bot is connecting to meeting...")
                elif status == "awaiting_admission":
                    print("Bot is waiting in the waiting room")
                    print("Please admit the bot to the meeting")
                elif status == "active":
                    print("Bot is actively transcribing!")
                elif status == "completed":
                    print("Transcription completed")
                    break
                elif status == "failed":
                    print(f"Bot failed: {data.get('payload', {}).get('error', 'Unknown error')}")
                    break

# Run the async function
asyncio.run(track_meeting_status())`}</code>
                </pre>
              </div>
            </TabsContent>

            <TabsContent value="curl" className="mt-4">
              <div className="space-y-2">
                <code className="text-xs text-muted-foreground">Note: WebSocket requires a WebSocket client</code>
                <p className="text-sm text-muted-foreground">
                  WebSocket connections cannot be made with curl. Use a WebSocket client library in your preferred language, or use a tool like <code className="bg-muted px-1 rounded">wscat</code>:
                </p>
                <pre className="bg-muted p-4 rounded-lg text-sm overflow-x-auto">
                  <code>{`# Install wscat: npm install -g wscat

# Connect to WebSocket
wscat -c "wss://your-api-url/ws?api_key=your-api-key"

# Subscribe to meeting
{"action":"subscribe","meetings":[{"platform":"google_meet","native_id":"abc-defg-hij"}]}

# You'll receive status updates like:
# {"type":"meeting.status","meeting":{"platform":"google_meet","native_id":"abc-defg-hij"},"payload":{"status":"requested"},"ts":"2024-01-01T12:00:00Z"}
# {"type":"meeting.status","meeting":{"platform":"google_meet","native_id":"abc-defg-hij"},"payload":{"status":"joining"},"ts":"2024-01-01T12:00:15Z"}
# ...`}</code>
                </pre>
              </div>
            </TabsContent>
          </Tabs>
        </CardContent>
      </Card>

      {/* Status Details */}
      <Card>
        <CardHeader>
          <CardTitle>Status Details</CardTitle>
          <CardDescription>
            Understanding each status and what actions to take
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="space-y-6">
            <div>
              <h3 className="text-sm font-semibold mb-2 flex items-center gap-2">
                <span className="px-2 py-1 bg-blue-100 dark:bg-blue-900 rounded text-xs">requested</span>
              </h3>
              <p className="text-sm text-muted-foreground mb-2">
                The bot container is being created and initialized. This typically takes 10-30 seconds.
              </p>
              <p className="text-xs text-muted-foreground">
                <strong>What to show:</strong> "Bot is starting up..." or a loading indicator
              </p>
            </div>

            <div>
              <h3 className="text-sm font-semibold mb-2 flex items-center gap-2">
                <span className="px-2 py-1 bg-blue-100 dark:bg-blue-900 rounded text-xs">joining</span>
              </h3>
              <p className="text-sm text-muted-foreground mb-2">
                The bot is launching the browser and connecting to the meeting platform. This typically takes 5-15 seconds.
              </p>
              <p className="text-xs text-muted-foreground">
                <strong>What to show:</strong> "Connecting to meeting..." or a connection indicator
              </p>
            </div>

            <div>
              <h3 className="text-sm font-semibold mb-2 flex items-center gap-2">
                <span className="px-2 py-1 bg-orange-100 dark:bg-orange-900 rounded text-xs">awaiting_admission</span>
              </h3>
              <p className="text-sm text-muted-foreground mb-2">
                The bot is in the meeting waiting room. The meeting host must admit the bot. This can take any amount of time depending on when the host checks the waiting room.
              </p>
              <p className="text-xs text-muted-foreground">
                <strong>What to show:</strong> "Please admit the bot to the meeting" with instructions to check the waiting room. This is a critical state that requires user action.
              </p>
            </div>

            <div>
              <h3 className="text-sm font-semibold mb-2 flex items-center gap-2">
                <span className="px-2 py-1 bg-green-100 dark:bg-green-900 rounded text-xs">active</span>
              </h3>
              <p className="text-sm text-muted-foreground mb-2">
                The bot is admitted to the meeting and actively transcribing. You will now receive <code className="bg-muted px-1 rounded">transcript.mutable</code> and <code className="bg-muted px-1 rounded">transcript.finalized</code> events.
              </p>
              <p className="text-xs text-muted-foreground">
                <strong>What to show:</strong> "Transcribing..." or a recording indicator. Start displaying transcript segments as they arrive.
              </p>
            </div>

            <div>
              <h3 className="text-sm font-semibold mb-2 flex items-center gap-2">
                <span className="px-2 py-1 bg-gray-100 dark:bg-gray-800 rounded text-xs">completed</span>
              </h3>
              <p className="text-sm text-muted-foreground mb-2">
                The transcription has finished. The meeting ended or the bot was stopped. Final transcripts are available via REST API.
              </p>
              <p className="text-xs text-muted-foreground">
                <strong>What to show:</strong> "Transcription completed" and provide a link to view/download the final transcript. You can safely disconnect the WebSocket.
              </p>
            </div>

            <div>
              <h3 className="text-sm font-semibold mb-2 flex items-center gap-2">
                <span className="px-2 py-1 bg-red-100 dark:bg-red-900 rounded text-xs">failed</span>
              </h3>
              <p className="text-sm text-muted-foreground mb-2">
                An error occurred. Common reasons include: admission timeout, meeting ended before bot joined, connection failure, or bot was removed.
              </p>
              <p className="text-xs text-muted-foreground">
                <strong>What to show:</strong> Error message with details. Allow the user to retry or create a new bot. You can safely disconnect the WebSocket.
              </p>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Best Practices */}
      <Card>
        <CardHeader>
          <CardTitle>Best Practices</CardTitle>
        </CardHeader>
        <CardContent>
          <ul className="space-y-2 text-sm">
            <li className="flex items-start gap-2">
              <span className="text-primary mt-1">•</span>
              <span>
                <strong>Handle all statuses:</strong> Make sure your UI handles all possible status values, including edge cases like <code className="bg-muted px-1 rounded">failed</code>.
              </span>
            </li>
            <li className="flex items-start gap-2">
              <span className="text-primary mt-1">•</span>
              <span>
                <strong>Show user action required:</strong> When status is <code className="bg-muted px-1 rounded">awaiting_admission</code>, clearly indicate that the user needs to admit the bot from the waiting room.
              </span>
            </li>
            <li className="flex items-start gap-2">
              <span className="text-primary mt-1">•</span>
              <span>
                <strong>Handle timeouts:</strong> If a bot stays in <code className="bg-muted px-1 rounded">requested</code> or <code className="bg-muted px-1 rounded">joining</code> for too long (e.g., 60+ seconds), show a warning or allow the user to cancel.
              </span>
            </li>
            <li className="flex items-start gap-2">
              <span className="text-primary mt-1">•</span>
              <span>
                <strong>Clean up on completion:</strong> When status becomes <code className="bg-muted px-1 rounded">completed</code> or <code className="bg-muted px-1 rounded">failed</code>, you can disconnect the WebSocket to save resources.
              </span>
            </li>
            <li className="flex items-start gap-2">
              <span className="text-primary mt-1">•</span>
              <span>
                <strong>Store status history:</strong> Keep a log of status changes with timestamps to help debug issues or show progress to users.
              </span>
            </li>
          </ul>
        </CardContent>
      </Card>

      {/* Related Documentation */}
      <Card>
        <CardHeader>
          <CardTitle>Related Documentation</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="space-y-2 text-sm">
            <Link href="/docs/ws" className="text-primary hover:underline inline-flex items-center gap-1">
              WebSocket API Overview <ExternalLink className="h-3 w-3" />
            </Link>
            <br />
            <Link href="/docs/ws/subscribe" className="text-primary hover:underline inline-flex items-center gap-1">
              Subscribing to Meetings <ExternalLink className="h-3 w-3" />
            </Link>
            <br />
            <Link href="/docs/ws/events" className="text-primary hover:underline inline-flex items-center gap-1">
              WebSocket Events Reference <ExternalLink className="h-3 w-3" />
            </Link>
            <br />
            <Link href="/docs/cookbook/live-transcripts" className="text-primary hover:underline inline-flex items-center gap-1">
              Receiving Live Transcripts <ExternalLink className="h-3 w-3" />
            </Link>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

