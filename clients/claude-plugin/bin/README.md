# bin

`vexa-listen`: the listener script Claude starts in the background (sh + curl). It waits on Vexa's `wait_for_signal` with a `listen_token` key and exits on the first line that names Claude, or when the call ends. Usage is in its header.
