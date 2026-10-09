import { Metadata } from "next";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import Link from "next/link";
import { ExternalLink } from "lucide-react";

export const metadata: Metadata = {
  title: "Get Status History | Vexa API Cookbook",
  description: "Learn how to retrieve meeting status transition history",
};

export default function GetStatusHistoryPage() {
  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Get Status History</h1>
        <p className="text-muted-foreground mt-2">
          Learn how to retrieve the complete history of status changes for a meeting, showing when and why the bot transitioned between states.
        </p>
      </div>

      {/* Overview */}
      <Card>
        <CardHeader>
          <CardTitle>Overview</CardTitle>
          <CardDescription>
            Status history is included in the meeting data when you fetch a meeting via the REST API. It shows all status transitions with timestamps, sources, and reasons.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="space-y-4">
            <div>
              <h3 className="text-sm font-semibold mb-2">Status History Structure</h3>
              <p className="text-sm text-muted-foreground mb-2">
                Each status transition includes:
              </p>
              <ul className="text-sm text-muted-foreground space-y-1 list-disc list-inside ml-2">
                <li><code className="bg-muted px-1 rounded">from</code> - Previous status</li>
                <li><code className="bg-muted px-1 rounded">to</code> - New status</li>
                <li><code className="bg-muted px-1 rounded">timestamp</code> - When the transition occurred (ISO 8601)</li>
                <li><code className="bg-muted px-1 rounded">source</code> - What triggered the change (e.g., "bot_callback", "user_api")</li>
                <li><code className="bg-muted px-1 rounded">reason</code> - Optional reason for the transition</li>
                <li><code className="bg-muted px-1 rounded">completion_reason</code> - Reason for completion (if status is "completed")</li>
              </ul>
            </div>
            <div>
              <h3 className="text-sm font-semibold mb-2">Where to Find It</h3>
              <p className="text-sm text-muted-foreground">
                Status history is available in the <code className="bg-muted px-1 rounded">data.status_transition</code> field when you fetch a meeting via:
              </p>
              <ul className="text-sm text-muted-foreground mt-2 space-y-1 list-disc list-inside ml-2">
                <li><code className="bg-muted px-1 rounded">GET /meetings</code> - List all meetings</li>
                <li><code className="bg-muted px-1 rounded">GET /transcripts/{`{platform}`}/{`{native_meeting_id}`}</code> - Get meeting with transcripts</li>
              </ul>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Implementation */}
      <Card>
        <CardHeader>
          <CardTitle>Implementation</CardTitle>
          <CardDescription>
            Fetch a meeting and access its status history
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
                  <code>{`// Get meeting with status history
async function getMeetingWithStatusHistory(platform, nativeMeetingId) {
  // Option 1: Get via transcripts endpoint (includes full meeting data)
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
    throw new Error(\`Failed to fetch meeting: \${response.statusText}\`);
  }
  
  const data = await response.json();
  
  // Status history is in data.data.status_transition
  const statusHistory = data.data?.status_transition || [];
  
  console.log(\`Found \${statusHistory.length} status transitions\`);
  
  // Display status history
  statusHistory.forEach((transition, index) => {
    console.log(\`\${index + 1}. \${transition.from} → \${transition.to}\`);
    console.log(\`   Time: \${transition.timestamp}\`);
    console.log(\`   Source: \${transition.source || 'unknown'}\`);
    if (transition.reason) {
      console.log(\`   Reason: \${transition.reason}\`);
    }
  });
  
  return statusHistory;
}

// Usage
const history = await getMeetingWithStatusHistory('google_meet', 'abc-defg-hij');

// Or get from meetings list
async function getAllMeetingsWithHistory() {
  const response = await fetch('https://your-api-url/meetings', {
    headers: {
      'X-API-Key': 'your-api-key',
      'Content-Type': 'application/json'
    }
  });
  
  const data = await response.json();
  
  // Each meeting in data.meetings has status_transition
  data.meetings.forEach(meeting => {
    const history = meeting.data?.status_transition || [];
    console.log(\`Meeting \${meeting.native_meeting_id} has \${history.length} status transitions\`);
  });
  
  return data.meetings;
}`}</code>
                </pre>
              </div>
            </TabsContent>

            <TabsContent value="python" className="mt-4">
              <div className="space-y-2">
                <code className="text-xs text-muted-foreground">Python Example</code>
                <pre className="bg-muted p-4 rounded-lg text-sm overflow-x-auto">
                  <code>{`import requests
from datetime import datetime

def get_meeting_with_status_history(platform, native_meeting_id, api_key):
    # Option 1: Get via transcripts endpoint (includes full meeting data)
    url = f"https://your-api-url/transcripts/{platform}/{native_meeting_id}"
    headers = {
        "X-API-Key": api_key,
        "Content-Type": "application/json"
    }
    
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    
    data = response.json()
    
    # Status history is in data['data']['status_transition']
    status_history = data.get('data', {}).get('status_transition', [])
    
    print(f"Found {len(status_history)} status transitions")
    
    # Display status history
    for i, transition in enumerate(status_history, 1):
        print(f"{i}. {transition['from']} → {transition['to']}")
        print(f"   Time: {transition['timestamp']}")
        print(f"   Source: {transition.get('source', 'unknown')}")
        if transition.get('reason'):
            print(f"   Reason: {transition['reason']}")
    
    return status_history

# Usage
history = get_meeting_with_status_history('google_meet', 'abc-defg-hij', 'your-api-key')

# Or get from meetings list
def get_all_meetings_with_history(api_key):
    url = "https://your-api-url/meetings"
    headers = {
        "X-API-Key": api_key,
        "Content-Type": "application/json"
    }
    
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    
    data = response.json()
    
    # Each meeting in data['meetings'] has status_transition
    for meeting in data['meetings']:
        history = meeting.get('data', {}).get('status_transition', [])
        print(f"Meeting {meeting['native_meeting_id']} has {len(history)} status transitions")
    
    return data['meetings']`}</code>
                </pre>
              </div>
            </TabsContent>

            <TabsContent value="curl" className="mt-4">
              <div className="space-y-2">
                <code className="text-xs text-muted-foreground">curl Example</code>
                <pre className="bg-muted p-4 rounded-lg text-sm overflow-x-auto">
                  <code>{`# Get meeting with status history via transcripts endpoint
curl -X GET \\
  "https://your-api-url/transcripts/google_meet/abc-defg-hij" \\
  -H "X-API-Key: your-api-key" \\
  -H "Content-Type: application/json"

# Response includes status_transition in data.data:
# {
#   "id": 123,
#   "platform": "google_meet",
#   "native_meeting_id": "abc-defg-hij",
#   "status": "active",
#   "data": {
#     "name": "Team Standup",
#     "status_transition": [
#       {
#         "from": null,
#         "to": "requested",
#         "timestamp": "2024-01-01T12:00:00Z",
#         "source": "user_api",
#         "reason": null
#       },
#       {
#         "from": "requested",
#         "to": "joining",
#         "timestamp": "2024-01-01T12:00:15Z",
#         "source": "bot_callback",
#         "reason": null
#       },
#       {
#         "from": "joining",
#         "to": "awaiting_admission",
#         "timestamp": "2024-01-01T12:00:30Z",
#         "source": "bot_callback",
#         "reason": null
#       },
#       {
#         "from": "awaiting_admission",
#         "to": "active",
#         "timestamp": "2024-01-01T12:01:00Z",
#         "source": "bot_callback",
#         "reason": null
#       }
#     ]
#   },
#   "segments": [...]
# }

# Or get from meetings list
curl -X GET \\
  "https://your-api-url/meetings" \\
  -H "X-API-Key: your-api-key" \\
  -H "Content-Type: application/json"`}</code>
                </pre>
              </div>
            </TabsContent>
          </Tabs>
        </CardContent>
      </Card>

      {/* Status Transition Example */}
      <Card>
        <CardHeader>
          <CardTitle>Status Transition Example</CardTitle>
          <CardDescription>
            Example status history showing a typical bot lifecycle
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="space-y-4">
            <pre className="bg-muted p-4 rounded-lg text-sm overflow-x-auto">
              <code>{JSON.stringify([
                {
                  from: null,
                  to: "requested",
                  timestamp: "2024-01-01T12:00:00Z",
                  source: "user_api",
                  reason: null
                },
                {
                  from: "requested",
                  to: "joining",
                  timestamp: "2024-01-01T12:00:15Z",
                  source: "bot_callback",
                  reason: null
                },
                {
                  from: "joining",
                  to: "awaiting_admission",
                  timestamp: "2024-01-01T12:00:30Z",
                  source: "bot_callback",
                  reason: null
                },
                {
                  from: "awaiting_admission",
                  to: "active",
                  timestamp: "2024-01-01T12:01:00Z",
                  source: "bot_callback",
                  reason: null
                },
                {
                  from: "active",
                  to: "completed",
                  timestamp: "2024-01-01T13:00:00Z",
                  source: "bot_callback",
                  completion_reason: "meeting_ended"
                }
              ], null, 2)}</code>
            </pre>
            <div className="text-sm text-muted-foreground">
              <p className="mb-2">This shows a complete bot lifecycle:</p>
              <ol className="list-decimal list-inside space-y-1 ml-2">
                <li>Bot created via user API (<code className="bg-muted px-1 rounded">requested</code>)</li>
                <li>Bot container started and connecting (<code className="bg-muted px-1 rounded">joining</code>)</li>
                <li>Bot in waiting room (<code className="bg-muted px-1 rounded">awaiting_admission</code>)</li>
                <li>Bot admitted and transcribing (<code className="bg-muted px-1 rounded">active</code>)</li>
                <li>Meeting ended (<code className="bg-muted px-1 rounded">completed</code>)</li>
              </ol>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Use Cases */}
      <Card>
        <CardHeader>
          <CardTitle>Use Cases</CardTitle>
        </CardHeader>
        <CardContent>
          <ul className="space-y-2 text-sm">
            <li className="flex items-start gap-2">
              <span className="text-primary mt-1">•</span>
              <span>
                <strong>Debugging:</strong> Understand why a bot got stuck or failed by examining the status transitions and their timestamps.
              </span>
            </li>
            <li className="flex items-start gap-2">
              <span className="text-primary mt-1">•</span>
              <span>
                <strong>Analytics:</strong> Track how long bots spend in each state to identify bottlenecks (e.g., long waiting room times).
              </span>
            </li>
            <li className="flex items-start gap-2">
              <span className="text-primary mt-1">•</span>
              <span>
                <strong>UI Display:</strong> Show a timeline of status changes to users, similar to the dashboard's Status History component.
              </span>
            </li>
            <li className="flex items-start gap-2">
              <span className="text-primary mt-1">•</span>
              <span>
                <strong>Monitoring:</strong> Alert when bots stay in certain states too long (e.g., <code className="bg-muted px-1 rounded">awaiting_admission</code> for more than 5 minutes).
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
            <Link href="/docs/rest/transcripts#get-transcripts" className="text-primary hover:underline inline-flex items-center gap-1">
              Get Transcripts API Reference <ExternalLink className="h-3 w-3" />
            </Link>
            <br />
            <Link href="/docs/rest/meetings#list-meetings" className="text-primary hover:underline inline-flex items-center gap-1">
              List Meetings API Reference <ExternalLink className="h-3 w-3" />
            </Link>
            <br />
            <Link href="/docs/cookbook/track-meeting-status" className="text-primary hover:underline inline-flex items-center gap-1">
              Track Meeting Status via WebSocket <ExternalLink className="h-3 w-3" />
            </Link>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

