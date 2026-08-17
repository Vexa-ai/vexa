# tests — context-stack

Real DDL on in-memory SQLite: CHECK and UNIQUE constraints are exercised, not described. No
docker, no network, no live stack.

| File | Level | Proves |
|---|---|---|
| `test_resolution.py` | L2 | the stack resolves in layer order for a member, a non-member, several groups and none; slots are pointers carrying their layer's rules; a bad pointer is denied, not fatal |
| `test_routing.py` | L2 | the routing table both ways — group → the queue, personal → context — plus the two refusing layers, and that 100 machine deltas accept none of themselves |
| `test_triage.py` | L2/L3 | accept applies, reject does not, a member may propose and not decide, a decision is final; and the four structural guards against machine acknowledgement |
| `test_enforcement.py` | L2 | seven routes into a group, all closed to a non-member; personal is not a small group; user-system is never sharable; the whole decision table's allow-list written out |
| `test_secrets.py` | L2 | set → rotate → metadata read, with the surface enumerated and every return searched for the value; the user-system layer holds no credentials ever |
| `test_api_surface.py` | L3 | the route table is exactly this; no response model has a field a secret could ride in; a real key comes back from no route; one accept route and no bulk form |
| `test_contract.py` | L1 | live output conforms to `context-stack.v1`, so the published shape cannot drift from the code while its goldens still pass |
| `test_repointing.py` | L2 | a user can be re-pointed at a different personal workspace, in the product path as well as free composition |

Two negative controls were run by hand against this suite and both were caught: a route that
returned key material (caught at three levels — the import graph, the response model, and the live
sweep), and a router that accepted its own proposal (which does not even import, by construction).
