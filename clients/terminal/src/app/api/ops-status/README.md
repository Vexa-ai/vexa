# api/ops-status

The maintenance-notice endpoint: serves the operator-written status file (see status.ts) so the UI can tell users an ops window is active. File-based on purpose — must keep answering while the backend stack is mid-deploy.
