- **One bot per meeting, even under concurrent requests (#1185).** Simultaneous requests for the
  same meeting now resolve to a single bot rather than occasionally admitting two, and a request
  for a meeting that is already live adopts the running bot instead of starting a second one.
