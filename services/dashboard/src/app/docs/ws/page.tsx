import { Metadata } from "next";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

export const metadata: Metadata = {
  title: "WebSocket API | Vexa API Documentation",
  description: "Real-time transcript streaming via WebSocket API",
};

export default function WebSocketPage() {
  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">WebSocket API</h1>
        <p className="text-muted-foreground mt-2">
          Connect to Vexa's WebSocket API to receive real-time transcript updates as meetings progress.
        </p>
      </div>

      <div className="prose prose-sm max-w-none space-y-6">
        <div>
          <h2>Overview</h2>
          <p>
            The WebSocket API allows you to receive live transcript segments as they are generated during meetings.
            Connect once and subscribe to multiple meetings for efficient real-time updates.
          </p>
        </div>

        <Card>
          <CardHeader>
            <CardTitle>Connection</CardTitle>
            <CardDescription>Connect to the WebSocket endpoint</CardDescription>
          </CardHeader>
          <CardContent>
            <p className="text-sm mb-4">
              WebSocket URL format (derived from your API URL):
            </p>
            <pre className="bg-muted p-4 rounded-lg text-sm">
              <code>wss://api.vexa.ai/ws?api_key=YOUR_API_KEY</code>
            </pre>
            <p className="text-sm text-muted-foreground mt-4">
              <strong>Note:</strong> Authentication is done via query parameter since browsers cannot set custom headers for WebSocket connections.
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Subscribe to Meetings</CardTitle>
            <CardDescription>Send a subscribe message after connecting</CardDescription>
          </CardHeader>
          <CardContent>
            <pre className="bg-muted p-4 rounded-lg text-sm">
              <code>{JSON.stringify({
                action: "subscribe",
                meetings: [
                  { platform: "google_meet", native_id: "abc-defg-hij" }
                ]
              }, null, 2)}</code>
            </pre>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Keepalive</CardTitle>
            <CardDescription>Send ping messages to keep the connection alive</CardDescription>
          </CardHeader>
          <CardContent>
            <pre className="bg-muted p-4 rounded-lg text-sm">
              <code>{JSON.stringify({ action: "ping" }, null, 2)}</code>
            </pre>
            <p className="text-sm text-muted-foreground mt-4">
              Send a ping every 25 seconds to maintain the connection. The server will respond with a pong message.
            </p>
          </CardContent>
        </Card>

        <div>
          <h2>Message Types</h2>
          <ul>
            <li><code>transcript.mutable</code> - Live transcript segments that may be updated</li>
            <li><code>transcript.finalized</code> - Final transcript segments</li>
            <li><code>meeting.status</code> - Meeting status updates</li>
            <li><code>subscribed</code> - Confirmation of successful subscription</li>
            <li><code>pong</code> - Response to ping messages</li>
            <li><code>error</code> - Error messages</li>
          </ul>
        </div>
      </div>

      <div className="space-y-4">
        <h2 className="text-2xl font-bold">Code Examples</h2>
        <Tabs defaultValue="javascript">
          <TabsList>
            <TabsTrigger value="javascript">JavaScript</TabsTrigger>
            <TabsTrigger value="python">Python</TabsTrigger>
          </TabsList>
          <TabsContent value="javascript">
            <pre className="bg-muted p-4 rounded-lg text-sm overflow-x-auto">
              <code>{`const ws = new WebSocket('wss://api.vexa.ai/ws?api_key=YOUR_API_KEY');

ws.onopen = () => {
  // Subscribe to meeting
  ws.send(JSON.stringify({
    action: 'subscribe',
    meetings: [
      { platform: 'google_meet', native_id: 'abc-defg-hij' }
    ]
  }));
  
  // Start ping interval
  setInterval(() => {
    ws.send(JSON.stringify({ action: 'ping' }));
  }, 25000);
};

ws.onmessage = (event) => {
  const message = JSON.parse(event.data);
  
  switch (message.type) {
    case 'transcript.mutable':
    case 'transcript.finalized':
      // Process transcript segments
      message.payload.segments.forEach(segment => {
        console.log(\`[\${segment.speaker}] \${segment.text}\`);
      });
      break;
    case 'meeting.status':
      console.log('Status:', message.payload.status);
      break;
    case 'subscribed':
      console.log('Subscribed to meetings:', message.meetings);
      break;
  }
};`}</code>
            </pre>
          </TabsContent>
          <TabsContent value="python">
            <pre className="bg-muted p-4 rounded-lg text-sm overflow-x-auto">
              <code>{`import asyncio
import websockets
import json
import os

async def connect_vexa():
    uri = f"wss://api.vexa.ai/ws?api_key={os.getenv('VEXA_API_KEY')}"
    
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
            
            if data["type"] in ["transcript.mutable", "transcript.finalized"]:
                for segment in data["payload"]["segments"]:
                    print(f"[{segment['speaker']}] {segment['text']}")
            elif data["type"] == "meeting.status":
                print(f"Status: {data['payload']['status']}")

asyncio.run(connect_vexa())`}</code>
            </pre>
          </TabsContent>
        </Tabs>
      </div>
    </div>
  );
}

