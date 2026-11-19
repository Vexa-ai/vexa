- **Teams links the bot can actually join.** A Teams thread id (`19:meeting_…@thread.v2`) sent
  without its link used to be accepted, and the bot then landed on the Microsoft sign-in page:
  the link's `context` carries the organizer's tenant, and the id alone does not. `POST /bots` now
  answers `422` and asks for the full `meeting_url`. A 17-digit Teams meeting ID now joins at
  `/meet/<id>` like the shorter ones, instead of the sign-in page. See
  [Send a bot](/how-to/send-a-bot).
