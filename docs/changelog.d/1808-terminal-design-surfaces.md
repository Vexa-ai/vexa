- **The terminal's surfaces use one set of controls, and confirm dialogs work (#1808).** The chat
  list, composer, pages panel, meetings, settings and sign-in now use the shared terminal controls.
  An answer's sources show as a citation list, with each date shown once. Every delete or
  stop-sharing confirmation is an in-app dialog that names the act; deleting a workspace asks you to
  type its name. Clicks on a dialog's buttons now reach them: before this fix, deleting a chat, a
  workspace or a planned meeting silently did nothing. Keyboard focus in a dialog no longer jumps
  back to Cancel when the chat list refreshes, and the delete-chat dialog opens on "Delete chat", so
  Enter deletes it and Esc cancels. Every keyboard-focused field shows a focus
  ring. See [Terminal UI design](/governance/ui-design).
