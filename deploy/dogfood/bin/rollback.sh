#!/usr/bin/env bash
# rollback.sh — put the previous set back in front of traffic, the same way it was taken out.
#
# This is why `deploy.sh` leaves the outgoing containers RUNNING under `-prev`, still on the
# network and still holding their host ports: a rollback that has to START something is a rollback
# that can fail for a brand-new reason at the worst possible moment. Everything here was serving
# minutes ago, so the whole operation is one alias move per server and one nginx reload — the same
# two switches the deploy made, pointed the other way, at the same zero-request cost.
#
# It rolls back exactly ONE step. `deploy.sh --retire` drops the `-prev` containers and with them
# the ability to do this at all; run it only once the new set has been believed for a while.
#
# USAGE
#   rollback.sh            roll every service in the state file back one step
#   rollback.sh <service>  roll one service back (agent-api | runtime | terminal)
#
# ORDER: TERMINAL FIRST, then the servers — the mirror image of the deploy, and for the same
# reason. The pairing rule runs in this direction too: rolling the server back first would leave
# the NEW terminal, which expects the new contract, in front of the OLD server. That is F77 exactly,
# reached by being helpful.

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
. ./bg-lib.sh

ONLY="${1:-}"
[ -f "$BG_STATE" ] || die "no state at $BG_STATE — this project has never been deployed by deploy.sh, so there is nothing to roll back to"

hdr "ROLLBACK — state generation $(state_get "s.get('generation','?')") · $(state_get "s.get('updated','?')")"
WORKERS_BEFORE=$(worker_set)

prev_of() { state_get "s.get('services',{}).get('$1',{}).get('prev',{}).get('container','')"; }
cur_of()  { state_get "s.get('services',{}).get('$1',{}).get('container','')"; }

TARGETS=()
for svc in terminal agent-api runtime; do
  [ -z "$ONLY" ] || [ "$ONLY" = "$svc" ] || continue
  p=$(prev_of "$svc"); [ -n "$p" ] || continue
  case "$p" in vexa-worker-*) die "state names a worker as a previous container: $p" ;; esac
  exists "$p"  || die "$svc: the previous container $p is gone — retired, or removed by hand. There is nothing to roll back to; deploy the previous tag instead."
  running "$p" || die "$svc: $p exists but is not running. Start it by hand and re-run — a rollback path that boots things is a rollback path that can fail."
  TARGETS+=("$svc")
  say "$svc: $(cur_of "$svc") -> $p"
done
[ ${#TARGETS[@]} -gt 0 ] || die "nothing to roll back${ONLY:+ for $ONLY}"

is_target() { printf '%s\n' "${TARGETS[@]}" | grep -qx "$1"; }

for svc in terminal agent-api runtime; do
  is_target "$svc" || continue
  prev=$(prev_of "$svc"); cur=$(cur_of "$svc"); alias="${BG_ALIAS[$svc]}"; port="${BG_PORT[$svc]}"
  hdr "$svc -> $prev"

  if [ -n "$alias" ]; then
    # Same ordering as the deploy: the incoming container takes the alias while the outgoing one
    # still holds it, so the name never resolves to nothing. The disconnect/reconnect is how an
    # alias is added at all (docker cannot amend a live endpoint) — and it re-publishes the host
    # port, possibly on a different number, which is why the port is re-read below.
    docker network disconnect "$BG_NETWORK" "$prev"
    docker network connect --alias "$alias" --alias "$prev" "$BG_NETWORK" "$prev"
    code=$(wait_net "$prev" "$port" "${BG_PATH[$svc]}" 30) \
      || die "$prev did not answer after taking $alias back (last: $code) — BOTH containers are on the alias now; look before touching anything else"
    ok "in-network  $prev on $alias -> $code"
  fi

  hp=$(wait_host_port "$prev" "$port") || die "$prev has no published host port — nginx would have nothing to point at"
  code=$(wait_host "http://$BG_LOOPBACK:$hp${BG_PATH[$svc]}" 30) || die "$prev is unreachable from the host on :$hp (last: $code)"
  ok "from host   http://$BG_LOOPBACK:$hp${BG_PATH[$svc]} -> $code"
  nginx_publish "$svc=$hp"

  if [ -n "$alias" ] && exists "$cur"; then
    drop_alias "$cur"
    ok "$cur released the alias — still running, still on its own host port"
  fi

  # The pins follow the traffic. A rolled-back service whose .env still names the new tag is a
  # rollback a later `docker compose up -d` silently undoes — which is how two live fixes were lost
  # on 2026-09-02 without anything reporting an error.
  tag=$(docker inspect "$prev" --format '{{.Config.Image}}' | sed 's/.*://')
  key="${BG_ENV_PIN[$svc]:-}"
  if [ -n "$key" ]; then env_pin "$key" "${tag%$BG_TERMINAL_SUFFIX}"; fi
  if [ "$svc" = runtime ]; then
    worker=$(docker inspect "$prev" --format '{{range .Config.Env}}{{println .}}{{end}}' | sed -n 's/^AGENT_WORKER_IMAGE=//p' | head -1)
    [ -n "$worker" ] && env_pin AGENT_WORKER_IMAGE "$worker" || true
  fi

  # One step back is all the evidence there is. The state records what was rolled OUT (so the run
  # is auditable) and clears `prev`, rather than claiming a second step back into a container
  # nobody has looked at since.
  python3 - "$BG_STATE" "$svc" "$prev" "${tag%$BG_TERMINAL_SUFFIX}" "$hp" <<'PY'
import datetime, json, sys
path, svc, prev, tag, hp = sys.argv[1:6]
st = json.load(open(path))
s = st["services"][svc]
st["services"][svc] = {"container": prev, "tag": tag, "host_port": hp,
                       "rolled_back_from": {"container": s.get("container"), "tag": s.get("tag"),
                                            "host_port": s.get("host_port")}}
st["generation"] = st.get("generation", 0) + 1
st["updated"] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
json.dump(st, open(path, "w"), indent=2)
PY
done

if [ -n "${BG_STABLE_URLS:-}" ]; then
  hdr "stable entries, after"
  for url in $BG_STABLE_URLS; do
    code=$(host_code "$url")
    [[ "$code" =~ ^[23] ]] && ok "$url -> $code" || die "$url -> $code after the rollback — the previous set is not serving; stop and look"
  done
fi

hdr "ROLLED BACK $(now)"
for svc in "${TARGETS[@]}"; do say "$svc -> $(cur_of "$svc") (host :$(state_get "s.get('services',{}).get('$svc',{}).get('host_port','')"))"; done
assert_no_workers_moved "$WORKERS_BEFORE"
say "the containers rolled OUT are still running; remove them by hand once you know why."
