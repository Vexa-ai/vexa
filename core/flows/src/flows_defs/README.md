# flows_defs — the flows themselves

Product behavior as DATA: each flow is a typed event trigger plus an ordered list of step
FUNCTIONS (a typo is a registration error, never a 2pm KeyError; strings exist only in the
database). One file per flow; reviewed like any product change; a new version is a new
registration — in-flight reactions keep the version stamped at admission, new events select the
newest (Registry.match).
