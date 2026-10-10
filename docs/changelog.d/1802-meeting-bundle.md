- **Move a meeting between deployments (#1802).** Export a meeting you own as one file (**Export** on
  the meeting, or `GET /meetings/{meeting_id}/export`) and import it into any other Vexa deployment
  (**Import meeting**, or `POST /meetings/import`, with a preview first). It carries the transcript,
  metadata, annotations and recordings, and nothing tied to the deployment it left; the imported
  meeting is yours alone. The format, `meeting-bundle.v1`, is a sealed contract documented for third
  parties. See [Move a meeting between deployments](/how-to/move-a-meeting).
