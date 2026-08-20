# app/meetings

The addressable meeting route. `/meetings/<id>` renders the same workbench shell as `/`, so an open
meeting has a URL that can be pasted, bookmarked and reloaded — the durable reference behind the
in-app (dockview) navigation, which is unchanged. The id is the meetings-domain row id; the shape and
the parsing contract live in `../meetingRoute.ts`.
