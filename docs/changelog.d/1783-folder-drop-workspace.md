- **Drop a folder to make a workspace (#1783).** Drag a folder from your computer onto the app, or
  choose **New workspace from folder…** in the chat's workspace menu, and a sheet shows the name,
  the file count and size, and what is left out before anything is uploaded. By default the folder
  becomes a new workspace that only you can see, committed once with you as the author. `.git`
  internals, dependency and build folders, system files, the folder's own `.gitignore` exclusions,
  `.env*` files and keys are never imported, and the sheet lists every file it leaves out. A folder
  that is a git repository is offered as a linked repository first. A folder dropped on a workspace
  is offered as a sub-folder of that workspace. The limits are 200 MB, 10,000 files and 25 MB per
  file; the administrator can change them with `folder_import_*` keys in `_global/POLICIES.md`.
  Agents can read an import's outcome with `workspace_folder_import_status`. See
  [Drop a folder](/how-to/chat-workspace#drop-a-folder).
