### Added

**The bot's two injection lifetimes are pinned by a test.** A `page.evaluate` watcher dies with its
document; an `addInitScript` one is re-injected into the next. The capture bundle and audio hook use
the second, the platform speaker watchers used the first — so a client that navigates after join
left audio flowing and speaker names silently dead. Proven with a Playwright double: no platform, no
account, no meeting.
