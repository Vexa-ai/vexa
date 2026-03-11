import { Metadata } from "next";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import Link from "next/link";
import { ExternalLink } from "lucide-react";

export const metadata: Metadata = {
  title: "Rename Meeting | Vexa API Cookbook",
  description: "Learn how to update meeting name and metadata using the REST API",
};

export default function RenameMeetingPage() {
  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Rename Meeting</h1>
        <p className="text-muted-foreground mt-2">
          Learn how to update meeting name and other metadata using the REST API.
        </p>
      </div>

      {/* Overview */}
      <Card>
        <CardHeader>
          <CardTitle>Overview</CardTitle>
          <CardDescription>
            Use the <code className="bg-muted px-1 rounded">PATCH /meetings/{`{platform}`}/{`{native_meeting_id}`}</code> endpoint to update meeting metadata including name, notes, participants, and languages.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="space-y-2 text-sm text-muted-foreground">
            <p>You can update:</p>
            <ul className="list-disc list-inside space-y-1 ml-2">
              <li><code className="bg-muted px-1 rounded">name</code> - Meeting title/name</li>
              <li><code className="bg-muted px-1 rounded">notes</code> - Meeting notes</li>
              <li><code className="bg-muted px-1 rounded">participants</code> - List of participant names</li>
              <li><code className="bg-muted px-1 rounded">languages</code> - List of language codes</li>
            </ul>
            <p className="mt-2">All fields are optional - only provided fields will be updated.</p>
          </div>
        </CardContent>
      </Card>

      {/* Implementation */}
      <Card>
        <CardHeader>
          <CardTitle>Implementation</CardTitle>
          <CardDescription>
            Update meeting name and other metadata
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
                  <code>{`// Rename a meeting
async function renameMeeting(platform, nativeMeetingId, newName) {
  const response = await fetch(
    \`https://your-api-url/meetings/\${platform}/\${nativeMeetingId}\`,
    {
      method: 'PATCH',
      headers: {
        'X-API-Key': 'your-api-key',
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        data: {
          name: newName
        }
      })
    }
  );
  
  if (!response.ok) {
    throw new Error(\`Failed to rename meeting: \${response.statusText}\`);
  }
  
  const updatedMeeting = await response.json();
  console.log(\`Meeting renamed to: \${updatedMeeting.data.name}\`);
  
  return updatedMeeting;
}

// Usage
await renameMeeting('google_meet', 'abc-defg-hij', 'Team Standup - Q1 Planning');

// Update multiple fields at once
async function updateMeetingMetadata(platform, nativeMeetingId, updates) {
  const response = await fetch(
    \`https://your-api-url/meetings/\${platform}/\${nativeMeetingId}\`,
    {
      method: 'PATCH',
      headers: {
        'X-API-Key': 'your-api-key',
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        data: {
          name: updates.name,
          notes: updates.notes,
          participants: updates.participants,
          languages: updates.languages
        }
      })
    }
  );
  
  return await response.json();
}

// Usage
await updateMeetingMetadata('google_meet', 'abc-defg-hij', {
  name: 'Weekly Team Sync',
  notes: 'Discussion about Q1 goals',
  participants: ['Alice', 'Bob', 'Charlie'],
  languages: ['en']
});`}</code>
                </pre>
              </div>
            </TabsContent>

            <TabsContent value="python" className="mt-4">
              <div className="space-y-2">
                <code className="text-xs text-muted-foreground">Python Example</code>
                <pre className="bg-muted p-4 rounded-lg text-sm overflow-x-auto">
                  <code>{`import requests

def rename_meeting(platform, native_meeting_id, new_name, api_key):
    url = f"https://your-api-url/meetings/{platform}/{native_meeting_id}"
    headers = {
        "X-API-Key": api_key,
        "Content-Type": "application/json"
    }
    payload = {
        "data": {
            "name": new_name
        }
    }
    
    response = requests.patch(url, headers=headers, json=payload)
    response.raise_for_status()
    
    updated_meeting = response.json()
    print(f"Meeting renamed to: {updated_meeting['data']['name']}")
    
    return updated_meeting

# Usage
rename_meeting('google_meet', 'abc-defg-hij', 'Team Standup - Q1 Planning', 'your-api-key')

# Update multiple fields at once
def update_meeting_metadata(platform, native_meeting_id, updates, api_key):
    url = f"https://your-api-url/meetings/{platform}/{native_meeting_id}"
    headers = {
        "X-API-Key": api_key,
        "Content-Type": "application/json"
    }
    payload = {
        "data": {
            "name": updates.get("name"),
            "notes": updates.get("notes"),
            "participants": updates.get("participants"),
            "languages": updates.get("languages")
        }
    }
    
    response = requests.patch(url, headers=headers, json=payload)
    response.raise_for_status()
    
    return response.json()

# Usage
update_meeting_metadata('google_meet', 'abc-defg-hij', {
    "name": "Weekly Team Sync",
    "notes": "Discussion about Q1 goals",
    "participants": ["Alice", "Bob", "Charlie"],
    "languages": ["en"]
}, 'your-api-key')`}</code>
                </pre>
              </div>
            </TabsContent>

            <TabsContent value="curl" className="mt-4">
              <div className="space-y-2">
                <code className="text-xs text-muted-foreground">curl Example</code>
                <pre className="bg-muted p-4 rounded-lg text-sm overflow-x-auto">
                  <code>{`# Rename a meeting
curl -X PATCH \\
  "https://your-api-url/meetings/google_meet/abc-defg-hij" \\
  -H "X-API-Key: your-api-key" \\
  -H "Content-Type: application/json" \\
  -d '{
    "data": {
      "name": "Team Standup - Q1 Planning"
    }
  }'

# Update multiple fields
curl -X PATCH \\
  "https://your-api-url/meetings/google_meet/abc-defg-hij" \\
  -H "X-API-Key: your-api-key" \\
  -H "Content-Type: application/json" \\
  -d '{
    "data": {
      "name": "Weekly Team Sync",
      "notes": "Discussion about Q1 goals",
      "participants": ["Alice", "Bob", "Charlie"],
      "languages": ["en"]
    }
  }'

# Response:
# {
#   "id": 123,
#   "platform": "google_meet",
#   "native_meeting_id": "abc-defg-hij",
#   "status": "active",
#   "data": {
#     "name": "Team Standup - Q1 Planning",
#     "notes": "Discussion about Q1 goals",
#     "participants": ["Alice", "Bob", "Charlie"],
#     "languages": ["en"]
#   }
# }`}</code>
                </pre>
              </div>
            </TabsContent>
          </Tabs>
        </CardContent>
      </Card>

      {/* Notes */}
      <Card>
        <CardHeader>
          <CardTitle>Important Notes</CardTitle>
        </CardHeader>
        <CardContent>
          <ul className="space-y-2 text-sm">
            <li className="flex items-start gap-2">
              <span className="text-primary mt-1">•</span>
              <span>
                <strong>Partial updates:</strong> Only fields you provide in the <code className="bg-muted px-1 rounded">data</code> object will be updated. Other fields remain unchanged.
              </span>
            </li>
            <li className="flex items-start gap-2">
              <span className="text-primary mt-1">•</span>
              <span>
                <strong>Meeting identification:</strong> Use <code className="bg-muted px-1 rounded">platform</code> and <code className="bg-muted px-1 rounded">native_meeting_id</code> to identify the meeting, not the internal database ID.
              </span>
            </li>
            <li className="flex items-start gap-2">
              <span className="text-primary mt-1">•</span>
              <span>
                <strong>Arrays:</strong> When updating <code className="bg-muted px-1 rounded">participants</code> or <code className="bg-muted px-1 rounded">languages</code>, provide the complete array - it will replace the existing array.
              </span>
            </li>
            <li className="flex items-start gap-2">
              <span className="text-primary mt-1">•</span>
              <span>
                <strong>Empty values:</strong> To clear a field, set it to <code className="bg-muted px-1 rounded">null</code> or an empty string (for strings) or empty array (for arrays).
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
            <Link href="/docs/rest/meetings#update-meeting-data" className="text-primary hover:underline inline-flex items-center gap-1">
              Update Meeting Data API Reference <ExternalLink className="h-3 w-3" />
            </Link>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

