- **Oversized requests are refused at the door.** The gateway answers `413` to a JSON, text or form
  body over 1 MiB before forwarding it (file uploads and `POST /meetings/import` keep their own
  limits), and `POST /bots` refuses a `bot_name` over 100 characters with a `422`. A bot whose
  container fails to start no longer leaves the created container behind.
