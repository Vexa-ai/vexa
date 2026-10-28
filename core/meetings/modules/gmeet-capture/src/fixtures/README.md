# Meet chat-panel fixtures

`meet-chat-<lang>.html` are the Google Meet in-call chat panel as `gmeet-chat-locales.test.ts` drives it, one
per UI language: `en`, `nl`, `de`, and `xx` (Hungarian labels, no icon ligatures — proves the structural layer
alone carries the reader and sender).

They are **constructed**, not captured from live Meet: the structure follows the hooks the reader targets
(`jsname`, `data-message-id`, `data-panel-id`, the `aria-live` list, the composer row) and the labels are each
language's wording. `de` also drops `data-panel-id` and the list's `jsname`, so the toggle is found by its icon
and the list by structure. A real capture (`document.body.outerHTML` of a call page with the chat panel open,
participant names and texts replaced) drops in as a new file with the same expectations.
