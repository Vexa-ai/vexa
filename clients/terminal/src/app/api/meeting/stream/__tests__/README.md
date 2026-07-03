# meeting stream route tests

Tests for the SSE proxy route (`../route.ts`): downstream stream lifecycle (close on upstream
end, error on upstream throw, abort upstream on client disconnect) and upstream-failure surfacing
as `stream-error` events.
