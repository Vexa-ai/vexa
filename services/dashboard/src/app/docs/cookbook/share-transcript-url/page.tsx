import { Metadata } from "next";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import Link from "next/link";
import { ExternalLink } from "lucide-react";

export const metadata: Metadata = {
  title: "Share Transcript URL | Vexa API Cookbook",
  description: "Learn how to create shareable transcript URLs for ChatGPT, Perplexity, and other AI tools",
};

export default function ShareTranscriptUrlPage() {
  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Share Transcript URL</h1>
        <p className="text-muted-foreground mt-2">
          Learn how to create shareable, public URLs for transcripts that can be used with AI tools like ChatGPT and Perplexity.
        </p>
      </div>

      {/* Overview */}
      <Card>
        <CardHeader>
          <CardTitle>Overview</CardTitle>
          <CardDescription>
            The transcript share API creates temporary, public URLs that allow AI tools to read transcripts directly from a URL. This is more efficient than copying and pasting large transcripts.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="space-y-4">
            <div>
              <h3 className="text-sm font-semibold mb-2">Use Cases</h3>
              <ul className="text-sm text-muted-foreground space-y-1 list-disc list-inside ml-2">
                <li>Share transcripts with ChatGPT using "Read from URL" feature</li>
                <li>Share transcripts with Perplexity for analysis</li>
                <li>Embed transcripts in documentation or reports</li>
                <li>Create temporary access links for team members</li>
              </ul>
            </div>
            <div>
              <h3 className="text-sm font-semibold mb-2">Key Features</h3>
              <ul className="text-sm text-muted-foreground space-y-1 list-disc list-inside ml-2">
                <li><strong>Public access:</strong> No authentication required to access the URL</li>
                <li><strong>Temporary:</strong> Links expire after a set time (default: 1 hour)</li>
                <li><strong>Secure:</strong> Share IDs are randomly generated and hard to guess</li>
                <li><strong>Text format:</strong> Transcripts are served as plain text (.txt) for easy reading</li>
              </ul>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Implementation */}
      <Card>
        <CardHeader>
          <CardTitle>Create Share URL</CardTitle>
          <CardDescription>
            Create a shareable transcript URL using the REST API
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
                  <code>{`// Create a share URL for a transcript
async function createTranscriptShare(platform, nativeMeetingId, meetingId, ttlSeconds) {
  const params = new URLSearchParams();
  if (meetingId) params.set('meeting_id', meetingId);
  if (ttlSeconds) params.set('ttl_seconds', String(ttlSeconds));
  const queryString = params.toString();
  
  const response = await fetch(
    \`https://your-api-url/transcripts/\${platform}/\${nativeMeetingId}/share\${queryString ? \`?\${queryString}\` : ''}\`,
    {
      method: 'POST',
      headers: {
        'X-API-Key': 'your-api-key',
        'Content-Type': 'application/json'
      }
    }
  );
  
  if (!response.ok) {
    throw new Error(\`Failed to create share URL: \${response.statusText}\`);
  }
  
  const share = await response.json();
  
  // share contains:
  // - share_id: Unique identifier for the share
  // - url: Full public URL to access the transcript
  // - expires_at: ISO timestamp when the link expires
  // - expires_in_seconds: Time until expiration
  
  console.log(\`Share URL created: \${share.url}\`);
  console.log(\`Expires at: \${share.expires_at}\`);
  
  return share;
}

// Usage
const share = await createTranscriptShare(
  'google_meet',
  'abc-defg-hij',
  123, // optional meeting_id
  7200 // optional: 2 hours TTL (default is 3600 = 1 hour)
);

// The share.url can be used directly:
// https://api.vexa.ai/public/transcripts/abc123def456.txt`}</code>
                </pre>
              </div>
            </TabsContent>

            <TabsContent value="python" className="mt-4">
              <div className="space-y-2">
                <code className="text-xs text-muted-foreground">Python Example</code>
                <pre className="bg-muted p-4 rounded-lg text-sm overflow-x-auto">
                  <code>{`import requests

def create_transcript_share(platform, native_meeting_id, meeting_id=None, ttl_seconds=None, api_key=None):
    url = f"https://your-api-url/transcripts/{platform}/{native_meeting_id}/share"
    headers = {
        "X-API-Key": api_key,
        "Content-Type": "application/json"
    }
    
    params = {}
    if meeting_id:
        params['meeting_id'] = meeting_id
    if ttl_seconds:
        params['ttl_seconds'] = ttl_seconds
    
    response = requests.post(url, headers=headers, params=params)
    response.raise_for_status()
    
    share = response.json()
    
    # share contains:
    # - share_id: Unique identifier for the share
    # - url: Full public URL to access the transcript
    # - expires_at: ISO timestamp when the link expires
    # - expires_in_seconds: Time until expiration
    
    print(f"Share URL created: {share['url']}")
    print(f"Expires at: {share['expires_at']}")
    
    return share

# Usage
share = create_transcript_share(
    'google_meet',
    'abc-defg-hij',
    meeting_id=123,  # optional
    ttl_seconds=7200,  # optional: 2 hours (default is 3600 = 1 hour)
    api_key='your-api-key'
)

# The share['url'] can be used directly:
# https://api.vexa.ai/public/transcripts/abc123def456.txt`}</code>
                </pre>
              </div>
            </TabsContent>

            <TabsContent value="curl" className="mt-4">
              <div className="space-y-2">
                <code className="text-xs text-muted-foreground">curl Example</code>
                <pre className="bg-muted p-4 rounded-lg text-sm overflow-x-auto">
                  <code>{`# Create a share URL
curl -X POST \\
  "https://your-api-url/transcripts/google_meet/abc-defg-hij/share?meeting_id=123&ttl_seconds=7200" \\
  -H "X-API-Key: your-api-key" \\
  -H "Content-Type: application/json"

# Response:
# {
#   "share_id": "abc123def456",
#   "url": "https://api.vexa.ai/public/transcripts/abc123def456.txt",
#   "expires_at": "2024-01-01T14:00:00Z",
#   "expires_in_seconds": 7200
# }

# Access the transcript (no authentication required):
curl "https://api.vexa.ai/public/transcripts/abc123def456.txt"`}</code>
                </pre>
              </div>
            </TabsContent>
          </Tabs>
        </CardContent>
      </Card>

      {/* Using with AI Tools */}
      <Card>
        <CardHeader>
          <CardTitle>Using Share URLs with AI Tools</CardTitle>
          <CardDescription>
            How to integrate share URLs with ChatGPT, Perplexity, and other AI tools
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="space-y-6">
            <div>
              <h3 className="text-sm font-semibold mb-2 flex items-center gap-2">
                <span className="px-2 py-1 bg-green-100 dark:bg-green-900 rounded text-xs">ChatGPT</span>
              </h3>
              <p className="text-sm text-muted-foreground mb-2">
                ChatGPT can read content from URLs. Create a prompt that includes the share URL:
              </p>
              <pre className="bg-muted p-4 rounded-lg text-sm overflow-x-auto">
                <code>{`// Create share URL
const share = await createTranscriptShare('google_meet', 'abc-defg-hij');

// Build ChatGPT URL with prompt
const prompt = \`Please read the meeting transcript from this URL and summarize the key points: \${share.url}\`;
const chatgptUrl = \`https://chatgpt.com/?hints=search&q=\${encodeURIComponent(prompt)}\`;

// Open in new window
window.open(chatgptUrl, '_blank', 'noopener,noreferrer');`}</code>
              </pre>
            </div>

            <div>
              <h3 className="text-sm font-semibold mb-2 flex items-center gap-2">
                <span className="px-2 py-1 bg-blue-100 dark:bg-blue-900 rounded text-xs">Perplexity</span>
              </h3>
              <p className="text-sm text-muted-foreground mb-2">
                Perplexity can also read from URLs. Use a similar approach:
              </p>
              <pre className="bg-muted p-4 rounded-lg text-sm overflow-x-auto">
                <code>{`// Create share URL
const share = await createTranscriptShare('google_meet', 'abc-defg-hij');

// Build Perplexity URL with prompt
const prompt = \`Please analyze the meeting transcript from this URL: \${share.url}\`;
const perplexityUrl = \`https://www.perplexity.ai/search?q=\${encodeURIComponent(prompt)}\`;

// Open in new window
window.open(perplexityUrl, '_blank', 'noopener,noreferrer');`}</code>
              </pre>
            </div>

            <div>
              <h3 className="text-sm font-semibold mb-2">Custom Prompts</h3>
              <p className="text-sm text-muted-foreground mb-2">
                You can customize the prompt to ask specific questions. Use <code className="bg-muted px-1 rounded">{`{url}`}</code> as a placeholder that will be replaced with the actual share URL:
              </p>
              <pre className="bg-muted p-4 rounded-lg text-sm overflow-x-auto">
                <code>{`// Example custom prompts
const prompts = {
  summary: \`Read the meeting transcript from {url} and provide a concise summary with action items.\`,
  qa: \`Read the meeting transcript from {url} and answer: What were the main decisions made?\`,
  analysis: \`Analyze the meeting transcript from {url} and identify key themes and discussion points.\`
};

// Replace {url} placeholder
const share = await createTranscriptShare('google_meet', 'abc-defg-hij');
const prompt = prompts.summary.replace(/{url}/g, share.url);
const chatgptUrl = \`https://chatgpt.com/?hints=search&q=\${encodeURIComponent(prompt)}\`;`}</code>
              </pre>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Complete Example */}
      <Card>
        <CardHeader>
          <CardTitle>Complete Example</CardTitle>
          <CardDescription>
            Full implementation for sharing transcripts with AI tools
          </CardDescription>
        </CardHeader>
        <CardContent>
          <pre className="bg-muted p-4 rounded-lg text-sm overflow-x-auto">
            <code>{`async function shareTranscriptWithAI(platform, nativeMeetingId, provider, customPrompt) {
  try {
    // 1. Create share URL
    const share = await createTranscriptShare(platform, nativeMeetingId);
    
    // 2. Build prompt with URL
    const defaultPrompt = \`Please read and analyze the meeting transcript from this URL: {url}\`;
    const prompt = (customPrompt || defaultPrompt).replace(/{url}/g, share.url);
    
    // 3. Build provider URL
    let providerUrl;
    if (provider === 'chatgpt') {
      providerUrl = \`https://chatgpt.com/?hints=search&q=\${encodeURIComponent(prompt)}\`;
    } else if (provider === 'perplexity') {
      providerUrl = \`https://www.perplexity.ai/search?q=\${encodeURIComponent(prompt)}\`;
    } else {
      throw new Error(\`Unsupported provider: \${provider}\`);
    }
    
    // 4. Open in new window
    window.open(providerUrl, '_blank', 'noopener,noreferrer');
    
    return {
      success: true,
      shareUrl: share.url,
      expiresAt: share.expires_at
    };
  } catch (error) {
    console.error('Failed to share transcript:', error);
    throw error;
  }
}

// Usage
await shareTranscriptWithAI(
  'google_meet',
  'abc-defg-hij',
  'chatgpt',
  'Read {url} and summarize the key action items.'
);`}</code>
          </pre>
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
                <strong>Set appropriate TTL:</strong> Use longer TTL (e.g., 7200 seconds = 2 hours) if you need the link to stay active longer, but remember links are public.
              </span>
            </li>
            <li className="flex items-start gap-2">
              <span className="text-primary mt-1">•</span>
              <span>
                <strong>Handle expiration:</strong> Check <code className="bg-muted px-1 rounded">expires_at</code> before sharing and warn users if the link is about to expire.
              </span>
            </li>
            <li className="flex items-start gap-2">
              <span className="text-primary mt-1">•</span>
              <span>
                <strong>Custom prompts:</strong> Create specific prompts for different use cases (summarization, Q&A, analysis) to get better results from AI tools.
              </span>
            </li>
            <li className="flex items-start gap-2">
              <span className="text-primary mt-1">•</span>
              <span>
                <strong>Public base URL:</strong> If your API is behind a private network, set <code className="bg-muted px-1 rounded">NEXT_PUBLIC_TRANSCRIPT_SHARE_BASE_URL</code> environment variable to point to a public URL where the share links will be accessible.
              </span>
            </li>
            <li className="flex items-start gap-2">
              <span className="text-primary mt-1">•</span>
              <span>
                <strong>Error handling:</strong> Always have a fallback (e.g., clipboard copy) in case share URL creation fails.
              </span>
            </li>
          </ul>
        </CardContent>
      </Card>

      {/* Response Format */}
      <Card>
        <CardHeader>
          <CardTitle>Response Format</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="space-y-4">
            <div>
              <h3 className="text-sm font-semibold mb-2">Share Response</h3>
              <pre className="bg-muted p-4 rounded-lg text-sm overflow-x-auto">
                <code>{JSON.stringify({
                  share_id: "abc123def456",
                  url: "https://api.vexa.ai/public/transcripts/abc123def456.txt",
                  expires_at: "2024-01-01T13:00:00Z",
                  expires_in_seconds: 3600
                }, null, 2)}</code>
              </pre>
            </div>
            <div>
              <h3 className="text-sm font-semibold mb-2">Transcript Content</h3>
              <p className="text-sm text-muted-foreground mb-2">
                When you access the share URL, you'll receive the transcript as plain text:
              </p>
              <pre className="bg-muted p-4 rounded-lg text-sm overflow-x-auto">
                <code>{`Meeting Transcript

Title: Team Standup
Date: 2024-01-01 12:00:00

[00:00:00] Alice: Hello everyone, welcome to today's meeting.
[00:00:05] Bob: Thanks for joining. Let's start with updates.
[00:00:10] Alice: I've completed the feature implementation.
[00:00:15] Bob: Great! What about the testing?
...`}</code>
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
            <Link href="/docs/rest/transcripts#create-transcript-share-link" className="text-primary hover:underline inline-flex items-center gap-1">
              Create Transcript Share Link API Reference <ExternalLink className="h-3 w-3" />
            </Link>
            <br />
            <Link href="/docs/rest/transcripts#get-transcripts" className="text-primary hover:underline inline-flex items-center gap-1">
              Get Transcripts API Reference <ExternalLink className="h-3 w-3" />
            </Link>
            <br />
            <Link href="/docs/cookbook/get-transcripts" className="text-primary hover:underline inline-flex items-center gap-1">
              Get Transcripts Cookbook <ExternalLink className="h-3 w-3" />
            </Link>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

