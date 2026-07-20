### Fixed

**Speaker watchers survive a navigation.** The browser bundle and the WebRTC audio hook are
re-injected into every document via `addInitScript`; the speaker watchers were installed once, by a
single `page.evaluate`, so their poll loops died with whichever document existed at that moment. A
client that navigates after join left the audio path alive and the name path silently dead — a
meeting transcribed correctly and attributed to nobody. Capture setup now re-arms on every main-frame
navigation, guarded so it installs one watcher per document and never two.
