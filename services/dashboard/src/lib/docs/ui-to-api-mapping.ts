/**
 * Mapping of UI actions to their corresponding API documentation pages
 */

export const UI_TO_API_DOCS: Record<string, string> = {
  // Join Meeting / Create Bot
  "join-meeting": "/docs/rest/bots#create-bot",
  "create-bot": "/docs/rest/bots#create-bot",
  
  // Stop Bot
  "stop-bot": "/docs/rest/bots#stop-bot",
  
  // Update Bot Config
  "update-bot-language": "/docs/rest/bots#update-bot-configuration",
  "update-bot-config": "/docs/rest/bots#update-bot-configuration",
  
  // Meetings
  "list-meetings": "/docs/rest/meetings#list-meetings",
  "get-meeting": "/docs/rest/meetings#get-meeting",
  "update-meeting-data": "/docs/rest/meetings#update-meeting-data",
  "update-meeting-notes": "/docs/rest/meetings#update-meeting-data",
  "update-meeting-title": "/docs/rest/meetings#update-meeting-data",
  
  // Transcripts
  "get-transcripts": "/docs/rest/transcripts#get-transcripts",
  "create-share-link": "/docs/rest/transcripts#create-transcript-share-link",
  
  // WebSocket
  "websocket-connect": "/docs/ws",
  "websocket-subscribe": "/docs/ws/subscribe",
  "websocket-events": "/docs/ws/events",
  
  // Admin - Users
  "admin-list-users": "/docs/admin/users#list-users",
  "admin-create-user": "/docs/admin/users#create-user",
  "admin-get-user": "/docs/admin/users#get-user",
  
  // Admin - Tokens
  "admin-create-token": "/docs/admin/users#create-api-token",
  "admin-revoke-token": "/docs/admin/tokens#revoke-token",
  
  // Bot Status
  "get-bot-status": "/docs/rest/bots#get-bot-status",
};

/**
 * Get API docs URL for a UI action
 */
export function getApiDocsUrl(action: string): string | null {
  return UI_TO_API_DOCS[action] || null;
}

