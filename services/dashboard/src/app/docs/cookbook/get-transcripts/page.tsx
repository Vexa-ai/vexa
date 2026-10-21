import { Metadata } from "next";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import Link from "next/link";
import { ExternalLink } from "lucide-react";

export const metadata: Metadata = {
  title: "Get Transcripts | Vexa API Cookbook",
  description: "Learn how to fetch transcripts via REST API and receive live updates via WebSocket",
};

export default function GetTranscriptsPage() {
  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Get Transcripts</h1>
        <p className="text-muted-foreground mt-2">
          Learn how to fetch transcripts using the REST API and receive live transcript updates via WebSocket.
        </p>
      </div>

      {/* Overview */}
      <Card>
        <CardHeader>
          <CardTitle>Overview</CardTitle>
          <CardDescription>
            There are two ways to get transcripts: <strong>REST API</strong> for fetching complete transcripts and <strong>WebSocket</strong> for real-time updates.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="space-y-4">
            <div>
              <h3 className="text-sm font-semibold mb-2">REST API</h3>
              <p className="text-sm text-muted-foreground">
                Use <code className="bg-muted px-1 rounded">GET /transcripts/{`{platform}`}/{`{native_meeting_id}`}</code> to fetch all transcript segments for a meeting. Best for:
              </p>
              <ul className="text-sm text-muted-foreground mt-2 space-y-1 list-disc list-inside">
                <li>Loading historical transcripts</li>
                <li>Bootstraping before WebSocket connection</li>
                <li>One-time transcript retrieval</li>
              </ul>
            </div>
            <div>
              <h3 className="text-sm font-semibold mb-2">WebSocket</h3>
              <p className="text-sm text-muted-foreground">
                Subscribe to meetings and receive <code className="bg-muted px-1 rounded">transcript.mutable</code> and <code className="bg-muted px-1 rounded">transcript.finalized</code> events. Best for:
              </p>
              <ul className="text-sm text-muted-foreground mt-2 space-y-1 list-disc list-inside">
                <li>Real-time transcript updates during active meetings</li>
                <li>Live transcription display</li>
                <li>Streaming transcript processing</li>
              </ul>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* REST API */}
      <Card>
        <CardHeader>
          <CardTitle>Fetch Transcripts via REST API</CardTitle>
          <CardDescription>
            Get all transcript segments for a meeting using the REST API
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
                  <code>{`// Fetch transcripts for a meeting
async function getTranscripts(platform, nativeMeetingId) {
  const response = await fetch(
    \`https://your-api-url/transcripts/\${platform}/\${nativeMeetingId}\`,
    {
      headers: {
        'X-API-Key': 'your-api-key',
        'Content-Type': 'application/json'
      }
    }
  );
  
  if (!response.ok) {
    throw new Error(\`Failed to fetch transcripts: \${response.statusText}\`);
  }
  
  const data = await response.json();
  
  // data.segments contains all transcript segments
  console.log(\`Found \${data.segments.length} transcript segments\`);
  
  // Each segment has:
  // - text: The transcribed text
  // - speaker: Speaker name
  // - start/end: Relative timestamps (seconds from meeting start)
  // - absolute_start_time/absolute_end_time: ISO timestamps
  // - language: Detected language code
  // - created_at: When the segment was created
  
  return data.segments;
}

// Usage
const segments = await getTranscripts('google_meet', 'abc-defg-hij');

// Display transcripts
segments.forEach(segment => {
  console.log(\`[\${segment.speaker}] \${segment.text}\`);
});`}</code>
                </pre>
              </div>
            </TabsContent>

            <TabsContent value="python" className="mt-4">
              <div className="space-y-2">
                <code className="text-xs text-muted-foreground">Python Example</code>
                <pre className="bg-muted p-4 rounded-lg text-sm overflow-x-auto">
                  <code>{`import requests

def get_transcripts(platform, native_meeting_id, api_key):
    url = f"https://your-api-url/transcripts/{platform}/{native_meeting_id}"
    headers = {
        "X-API-Key": api_key,
        "Content-Type": "application/json"
    }
    
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    
    data = response.json()
    
    # data['segments'] contains all transcript segments
    print(f"Found {len(data['segments'])} transcript segments")
    
    # Each segment has:
    # - text: The transcribed text
    # - speaker: Speaker name
    # - start/end: Relative timestamps (seconds from meeting start)
    # - absolute_start_time/absolute_end_time: ISO timestamps
    # - language: Detected language code
    # - created_at: When the segment was created
    
    return data['segments']

# Usage
segments = get_transcripts('google_meet', 'abc-defg-hij', 'your-api-key')

# Display transcripts
for segment in segments:
    print(f"[{segment['speaker']}] {segment['text']}")`}</code>
                </pre>
              </div>
            </TabsContent>

            <TabsContent value="curl" className="mt-4">
              <div className="space-y-2">
                <code className="text-xs text-muted-foreground">curl Example</code>
                <pre className="bg-muted p-4 rounded-lg text-sm overflow-x-auto">
                  <code>{`# Fetch transcripts
curl -X GET \\
  "https://your-api-url/transcripts/google_meet/abc-defg-hij" \\
  -H "X-API-Key: your-api-key" \\
  -H "Content-Type: application/json"

# Response:
# {
#   "id": 123,
#   "platform": "google_meet",
#   "native_meeting_id": "abc-defg-hij",
#   "status": "completed",
#   "segments": [
#     {
#       "start": 0.0,
#       "end": 5.2,
#       "text": "Hello everyone, welcome to today's meeting.",
#       "speaker": "Alice",
#       "language": "en",
#       "absolute_start_time": "2024-01-01T12:00:00Z",
#       "absolute_end_time": "2024-01-01T12:00:05Z",
#       "created_at": "2024-01-01T12:00:05Z"
#     }
#   ]
# }`}</code>
                </pre>
              </div>
            </TabsContent>
          </Tabs>
        </CardContent>
      </Card>

      {/* WebSocket */}
      <Card>
        <CardHeader>
          <CardTitle>Receive Live Transcripts via WebSocket</CardTitle>
          <CardDescription>
            Subscribe to meetings and receive transcript events in real-time
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="space-y-4">
            <p className="text-sm text-muted-foreground">
              For detailed WebSocket implementation, see the <Link href="/docs/cookbook/live-transcripts" className="text-primary hover:underline">Live Transcripts cookbook</Link>.
            </p>
            
            <div className="space-y-2">
              <h4 className="text-sm font-semibold">Quick Example</h4>
              <pre className="bg-muted p-4 rounded-lg text-sm overflow-x-auto">
                <code>{`// Connect and subscribe
const ws = new WebSocket('wss://your-api-url/ws?api_key=your-api-key');

ws.onopen = () => {
  ws.send(JSON.stringify({
    action: 'subscribe',
    meetings: [{ platform: 'google_meet', native_id: 'abc-defg-hij' }]
  }));
};

ws.onmessage = (event) => {
  const message = JSON.parse(event.data);
  
  if (message.type === 'transcript.mutable') {
    // Live segments that may be updated
    message.payload.segments.forEach(segment => {
      console.log(\`[LIVE] [\${segment.speaker}] \${segment.text}\`);
    });
  } else if (message.type === 'transcript.finalized') {
    // Final segments that won't change
    message.payload.segments.forEach(segment => {
      console.log(\`[FINAL] [\${segment.speaker}] \${segment.text}\`);
    });
  }
};`}</code>
              </pre>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Best Practice: Bootstrap Pattern */}
      <Card>
        <CardHeader>
          <CardTitle>Best Practice: Bootstrap Pattern</CardTitle>
          <CardDescription>
            Combine REST and WebSocket for the best experience
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="space-y-4">
            <p className="text-sm text-muted-foreground">
              For active meetings, use a bootstrap pattern:
            </p>
            <ol className="text-sm space-y-2 list-decimal list-inside">
              <li className="text-muted-foreground">
                <strong>Bootstrap:</strong> Fetch existing transcripts via REST API first
              </li>
              <li className="text-muted-foreground">
                <strong>Connect:</strong> Establish WebSocket connection and subscribe
              </li>
              <li className="text-muted-foreground">
                <strong>Merge:</strong> Use <code className="bg-muted px-1 rounded">absolute_start_time</code> as unique ID to merge REST and WebSocket segments
              </li>
              <li className="text-muted-foreground">
                <strong>Update:</strong> Replace mutable segments with finalized ones as they arrive
              </li>
            </ol>
            
            <div className="mt-4">
              <h4 className="text-sm font-semibold mb-2">Example Implementation</h4>
              <pre className="bg-muted p-4 rounded-lg text-sm overflow-x-auto">
                <code>{`// 1. Bootstrap from REST
const bootstrapSegments = await getTranscripts(platform, nativeId);
const transcriptMap = new Map();

// Store segments by absolute_start_time (unique ID)
bootstrapSegments.forEach(seg => {
  transcriptMap.set(seg.absolute_start_time, seg);
});

// 2. Connect WebSocket
const ws = new WebSocket('wss://your-api-url/ws?api_key=your-api-key');
ws.onopen = () => {
  ws.send(JSON.stringify({
    action: 'subscribe',
    meetings: [{ platform, native_id: nativeId }]
  }));
};

// 3. Handle updates
ws.onmessage = (event) => {
  const message = JSON.parse(event.data);
  
  if (message.type === 'transcript.mutable' || message.type === 'transcript.finalized') {
    message.payload.segments.forEach(seg => {
      const key = seg.absolute_start_time;
      const existing = transcriptMap.get(key);
      
      // Update if new or if this version is newer
      if (!existing || new Date(seg.updated_at) > new Date(existing.updated_at)) {
        transcriptMap.set(key, seg);
        updateUI(Array.from(transcriptMap.values()).sort((a, b) => 
          new Date(a.absolute_start_time) - new Date(b.absolute_start_time)
        ));
      }
    });
  }
};`}</code>
              </pre>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Related Documentation */}
      <Card>
        <CardHeader>
          <CardTitle>Related Documentation</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="space-y-2 text-sm">
            <Link href="/docs/rest/transcripts" className="text-primary hover:underline inline-flex items-center gap-1">
              Transcripts REST API Reference <ExternalLink className="h-3 w-3" />
            </Link>
            <br />
            <Link href="/docs/cookbook/live-transcripts" className="text-primary hover:underline inline-flex items-center gap-1">
              Live Transcripts via WebSocket <ExternalLink className="h-3 w-3" />
            </Link>
            <br />
            <Link href="/docs/ws/events" className="text-primary hover:underline inline-flex items-center gap-1">
              WebSocket Events Reference <ExternalLink className="h-3 w-3" />
            </Link>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

