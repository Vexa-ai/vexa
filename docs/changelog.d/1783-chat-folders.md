- **Chat folders in the rail: Pinned, your folders, Ungrouped and Hidden, with drag and drop.** The
  chat list is drawn in your own sections, each folding shut and capped with *Show N more*. Make a
  folder from the list's header, drag chats onto it (press and hold on a phone), or use **Move to…** in
  a chat's menu; deleting a folder moves its chats to Ungrouped and deletes none. **Hidden is a folder
  now**, folded at the bottom: hiding a chat moves it there and dragging it out unhides it; the
  Active / All / Hidden tabs are gone, and chats you had hidden or pinned are filed in Hidden and
  Pinned the first time you sign in. Folders are per person, kept by agent-api in the session store
  (two new tables, `agent_session_folder` and `agent_session_filing`), so on a shared chat each person files
  it in their own folders. The agent has the same verbs as tools — `chat_folders_list`,
  `chat_folder_create`, `chat_folder_update`, `chat_folder_delete`, `chat_move` — so *"put the Acme
  chats in an Acme folder"* works from the composer. Without the session store the folder routes answer
  `503 folders_unavailable` and the rail keeps Pinned and Hidden from the old marks. See
  [Manage your chats](/how-to/chat-controls).
