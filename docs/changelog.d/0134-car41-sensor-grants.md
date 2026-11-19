- **A calendar sensor reads your calendar only under your grant.** The credential broker's `sensors`
  key no longer reads the calendar of whoever flows names: each read needs a live grant from the
  sensor's person on that Google Calendar connection, scope calendar read, for at most 90 days. The
  person gives it in a chat they are watching (`calendar_sensor_grant`), lists it
  (`calendar_sensor_grants`) and ends it (`calendar_sensor_grant_revoke`); a routine, a background run
  or a chat other people read cannot give one, and neither can the sensors key. A sensor without a
  grant stops with the reason *this sensor may not read your calendar*. On upgrade the broker gives a
  30-day grant, once, to each calendar its own audit shows a sensor read in the 14 days before, so
  running sensors keep working while their people re-grant. `credential-broker.v1` gains
  `/api/sensor-grants` and the `sensor_grant_required` reason. See [Sensors](/flows/sensors).
