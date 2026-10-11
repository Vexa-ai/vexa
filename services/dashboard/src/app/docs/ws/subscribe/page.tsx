import { Metadata } from "next";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

export const metadata: Metadata = {
  title: "WebSocket Subscribe | Vexa API Documentation",
  description: "How to subscribe to meetings for real-time transcript updates",
};

export default function WebSocketSubscribePage() {
  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Subscribe to Meetings</h1>
        <p className="text-muted-foreground mt-2">
          Learn how to subscribe to meetings to receive real-time transcript updates via WebSocket.
        </p>
      </div>

      <div className="prose prose-sm max-w-none space-y-6">
        <div>
          <h2>Subscription Flow</h2>
          <ol>
            <li>Connect to the WebSocket endpoint</li>
            <li>Wait for connection to open</li>
            <li>Send a subscribe message with meeting details</li>
            <li>Receive confirmation and start receiving events</li>
          </ol>
        </div>

        <Card>
          <CardHeader>
            <CardTitle>Subscribe Message Format</CardTitle>
            <CardDescription>Send this message after connecting</CardDescription>
          </CardHeader>
          <CardContent>
            <pre className="bg-muted p-4 rounded-lg text-sm overflow-x-auto">
              <code>{JSON.stringify({
                action: "subscribe",
                meetings: [
                  {
                    platform: "google_meet",
                    native_id: "abc-defg-hij"
                  }
                ]
              }, null, 2)}</code>
            </pre>
            <p className="text-sm text-muted-foreground mt-4">
              You can subscribe to multiple meetings in a single message by including multiple entries in the <code>meetings</code> array.
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Subscription Confirmation</CardTitle>
            <CardDescription>You'll receive this after subscribing</CardDescription>
          </CardHeader>
          <CardContent>
            <pre className="bg-muted p-4 rounded-lg text-sm overflow-x-auto">
              <code>{JSON.stringify({
                type: "subscribed",
                meetings: [123, 124]
              }, null, 2)}</code>
            </pre>
            <p className="text-sm text-muted-foreground mt-4">
              The <code>meetings</code> array contains the internal meeting IDs that you're now subscribed to.
            </p>
          </CardContent>
        </Card>

        <div>
          <h2>Code Examples</h2>
          <Tabs defaultValue="javascript">
            <TabsList>
              <TabsTrigger value="javascript">JavaScript</TabsTrigger>
              <TabsTrigger value="python">Python</TabsTrigger>
            </TabsList>
            <TabsContent value="javascript">
              <pre className="bg-muted p-4 rounded-lg text-sm overflow-x-auto">
                <code>{`const ws = new WebSocket('wss://api.vexa.ai/ws?api_key=YOUR_API_KEY');

ws.onopen = () => {
  // Subscribe to a meeting
  ws.send(JSON.stringify({
    action: 'subscribe',
    meetings: [
      { platform: 'google_meet', native_id: 'abc-defg-hij' }
    ]
  }));
};

ws.onmessage = (event) => {
  const message = JSON.parse(event.data);
  
  if (message.type === 'subscribed') {
    console.log('Subscribed to meetings:', message.meetings);
  }
};`}</code>
              </pre>
            </TabsContent>
            <TabsContent value="python">
              <pre className="bg-muted p-4 rounded-lg text-sm overflow-x-auto">
                <code>{`import asyncio
import websockets
import json

async def subscribe():
    uri = "wss://api.vexa.ai/ws?api_key=YOUR_API_KEY"
    
    async with websockets.connect(uri) as websocket:
        # Subscribe to meeting
        await websocket.send(json.dumps({
            "action": "subscribe",
            "meetings": [
                {"platform": "google_meet", "native_id": "abc-defg-hij"}
            ]
        }))
        
        # Wait for confirmation
        response = await websocket.recv()
        data = json.loads(response)
        
        if data["type"] == "subscribed":
            print(f"Subscribed to meetings: {data['meetings']}")

asyncio.run(subscribe())`}</code>
              </pre>
            </TabsContent>
          </Tabs>
        </div>
      </div>
    </div>
  );
}

