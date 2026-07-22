- **`POST /bots` rejects a `native_meeting_id` carrying URL characters (#892).** A meeting id
  with `?`, `#`, `&`, `=`, `/`, or a space (e.g. a Teams passcode accidentally left on the id,
  `397421056486982?p=…`) now returns a typed `422` at intake instead of building a broken join URL
  and storing an unfindable record. Pass any passcode in the `passcode` field, or supply the full
  `meeting_url`. Bare ids across platforms (Meet dash-codes, Zoom digits, Teams `19:…@thread.v2`)
  are unaffected.
