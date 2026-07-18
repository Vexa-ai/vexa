- **Google Meet auth-session guard is now pinned by an offline test (#756).** `session.test.ts`
  drives `joinGoogleMeeting`'s authenticated branch against fabricated lobby fixtures: a
  signed-out guest lobby must throw the typed `auth_session_missing` refusal and a signed-in
  not-pre-admitted lobby must knock. Deleting the guard turns the join module's suite red —
  the guard is no longer carried only by the live leg.
