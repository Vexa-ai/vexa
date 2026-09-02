#!/usr/bin/env bash
# deploy.sh — the invisible swap. PRD decision 39, founder 2026-09-02: *"what's this out / in? why
# we need this?"*
#
# The "out / in" ritual existed for two reasons, both OURS:
#   F20  a container recreated under an open tab fails the request in flight ("fetch failed"
#        mid-stream, which the founder read as the product breaking);
#   F55  the terminal and the server must change together, and
#   F77  in either direction — a new client on an old server, or a client whose every request the
#        running server rejects with a 422.
# Neither reason needs a person. This script removes both:
#
#   1. the new container starts BESIDE the old, under a versioned name, and takes no traffic until
#      it has answered its health check from inside the network AND from the host;
#   2. traffic then moves atomically — the compose network ALIAS for agent-api/runtime (so the
#      terminal proxy, the rig and the flows lanes resolve the new one with nothing restarted) and
#      an nginx UPSTREAM + reload for the terminal (so app.dev.vexa.ai never drops a request);
#   3. the outgoing container stays UP, renamed `-prev`, for exactly one `rollback.sh` step;
#   4. the pairing rule is enforced by the machine (GUARD 4), not by a human reading a diff;
#   5. and the open tab is told by the version bar that a new build is ready.
#
# Order, unchanged from the window ritual it replaces: the server pair first, the terminal LAST.
# Workers (`vexa-worker-*`) are never touched — a turn in flight finishes in the container it
# started in, and a new worker image applies to the next spawn.
#
# USAGE
#   deploy.sh --check <tag-set>     every guard against the real target; changes nothing
#   deploy.sh <tag-set>             the swap
#   deploy.sh --retire              remove the `-prev` containers of the last swap
#
#   <tag-set> is one tag for everything —      deploy.sh line-6bec34db4
#   or a per-service list —                    deploy.sh agent-api=line-a,terminal=line-b
#   The terminal image is <tag><BG_TERMINAL_SUFFIX> (default `-minutes`).
#
# See bg-lib.sh for the standing rule, the exhaustive list of mutating actions, and every guard.

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
. ./bg-lib.sh

MODE=swap
case "${1:-}" in
  --check)  MODE=check; shift ;;
  --retire) MODE=retire; shift ;;
  -h|--help|"") sed -n '2,36p' "$0" | sed 's/^# \{0,1\}//'; exit 1 ;;
esac

# ── RETIRE ──────────────────────────────────────────────────────────────────────────────────────
if [ "$MODE" = retire ]; then
  hdr "RETIRE — dropping the previous set"
  n=0
  for svc in "${BG_SERVICES[@]}"; do
    prev=$(state_get "s.get('services',{}).get('$svc',{}).get('prev',{}).get('container','')")
    [ -n "$prev" ] || continue
    case "$prev" in vexa-worker-*) die "refusing to touch a worker: $prev" ;; esac
    if exists "$prev"; then docker rm -f "$prev" >/dev/null; ok "removed $prev"; n=$((n+1))
    else say "$svc: $prev is already gone"; fi
  done
  say "retired $n container(s); rollback of the last swap is no longer possible"
  exit 0
fi

# ── THE TAG SET ─────────────────────────────────────────────────────────────────────────────────
declare -A TAG=()
IFS=',' read -ra parts <<<"${1:?a tag set is required}"
for p in "${parts[@]}"; do
  if [[ "$p" == *=* ]]; then
    svc="${p%%=*}"; [ -n "${BG_PORT[$svc]:-}" ] || die "unknown service '$svc' (known: ${BG_SERVICES[*]})"
    TAG[$svc]="${p#*=}"
  else
    for svc in "${BG_SERVICES[@]}"; do TAG[$svc]="$p"; done
  fi
done
PAIRS=(); for svc in "${BG_SERVICES[@]}"; do [ -n "${TAG[$svc]:-}" ] && PAIRS+=("$svc=${TAG[$svc]}") || true; done
[ ${#PAIRS[@]} -gt 0 ] || die "no services in the tag set"

hdr "TARGET"
say "project=$BG_PROJECT  network=$BG_NETWORK  dir=$BG_COMPOSE_DIR"
for p in "${PAIRS[@]}"; do say "  ${p%%=*} → $(image_for "${p%%=*}" "${p#*=}")"; done

WORKERS_BEFORE=$(worker_set)

# ── GUARDS ──────────────────────────────────────────────────────────────────────────────────────
hdr "GUARDS"
guard_project
guard_images "${PAIRS[@]}"
if [ -n "${TAG[terminal]:-}" ]; then
  guard_terminal_variant "$(image_for terminal "${TAG[terminal]}")"
  guard_nginx_adopted
fi
if [ -n "${BG_STABLE_URLS:-}" ]; then
  # shellcheck disable=SC2086
  guard_host_ports $BG_STABLE_URLS
else
  say "GUARD 5 — BG_STABLE_URLS unset; the stable entries are not asserted before/after (set it)"
fi

if [ "$MODE" = check ]; then
  hdr "CHECK"
  if [ -n "${TAG[terminal]:-}" ]; then
    # GUARD 4 needs the agent-api that WILL be serving; on --check that is the one serving now.
    aport=$(state_get "s.get('services',{}).get('agent-api',{}).get('host_port','')")
    if [ -n "$aport" ]; then
      guard_pairing "$(image_for terminal "${TAG[terminal]}")" "http://$BG_LOOPBACK:$aport"
    else
      warn "GUARD 4 — no agent-api host port on record; pairing is checked during the swap, after the server pair moves"
    fi
  fi
  ok "--check: every guard exercised against the real target, nothing changed."
  exit 0
fi

# ── FAILURE HANDLING ────────────────────────────────────────────────────────────────────────────
# PENDING holds only containers this run created that have NOT yet taken traffic. The moment one
# does, it leaves the list — removing a container that is serving would turn a failed deploy into
# an outage, which is the opposite of the trade this script exists to make.
PENDING=()
DONE_OK=0
release_pending() { local keep=() c; for c in "${PENDING[@]:-}"; do [ "$c" = "$1" ] || keep+=("$c"); done; PENDING=("${keep[@]:-}"); }
on_exit() {
  [ "$DONE_OK" = 1 ] && return 0
  local c
  for c in "${PENDING[@]:-}"; do
    [ -n "$c" ] || continue
    case "$c" in vexa-worker-*) continue ;; esac
    if exists "$c"; then docker rm -f "$c" >/dev/null 2>&1 || true; warn "removed the not-yet-serving container this run created: $c"; fi
  done
  warn "deploy did not complete — nothing that was serving before this run was changed by the cleanup"
}
trap on_exit EXIT

# Start <service> at <tag> beside whatever is serving.
#
# The host port is EPHEMERAL (`-p 127.0.0.1:0:<port>`) on purpose. The stable entry a consumer uses
# is the nginx upstream, never the container's own binding: a container that owns :15401 cannot be
# replaced while it is up, and freeing that port first IS the downtime this script exists to remove.
start_beside() {  # <service> <tag> <old-container> -> echoes the new container name
  local svc="$1" tag="$2" old="$3" name img port envfile
  name=$(container_for "$svc" "$tag"); img=$(image_for "$svc" "$tag"); port="${BG_PORT[$svc]}"
  case "$name" in vexa-worker-*) die "refusing a name in the worker namespace: $name" ;; esac
  ! exists "$name" || die "$name already exists — a previous deploy of this tag is still around. Check what it is, then \`docker rm -f $name\`, or deploy a different tag."

  envfile=$(mktemp)
  # The container-level env ONLY (image defaults excluded) — see container_env in bg-lib.sh for
  # why replaying an image's own baked env onto a NEW image is how F67 happens again.
  container_env "$old" | grep -v '^VEXA_BUILD_SHA=' > "$envfile" || true
  # The build stamp GET /api/version reports and the reload bar compares.
  printf 'VEXA_BUILD_SHA=%s\n' "$tag" >> "$envfile"
  if [ "$svc" = runtime ]; then
    # There is no agent-worker container — workers are spawned per dispatch — so "swap the worker"
    # IS this variable, and it applies to the NEXT spawn only.
    grep -v '^AGENT_WORKER_IMAGE=' "$envfile" > "$envfile.2" || true; mv "$envfile.2" "$envfile"
    printf 'AGENT_WORKER_IMAGE=%s\n' "${BG_WORKER_IMAGE:-${BG_IMAGE_PREFIX}agent-worker:$tag}" >> "$envfile"
  fi

  local mounts=(); mapfile -t mounts < <(container_mounts "$old" | tr ' ' '\n' | grep -v '^$' || true)

  docker run -d --name "$name" \
    --network "$BG_NETWORK" --network-alias "$name" \
    -p "$BG_LOOPBACK:0:$port" \
    --env-file "$envfile" \
    --restart unless-stopped \
    --label ai.vexa.bluegreen.project="$BG_PROJECT" \
    --label ai.vexa.bluegreen.service="$svc" \
    --label ai.vexa.bluegreen.tag="$tag" \
    ${mounts[@]:+"${mounts[@]}"} \
    "$img" >/dev/null
  rm -f "$envfile"
  echo "$name"
}

prove_new() {  # <service> <name> -> echoes the ephemeral host port
  # ONE `local` PER LINE, deliberately. bash expands every assignment word in a `local` statement
  # against the scope OUTSIDE it, so `local svc="$1" port="${BG_PORT[$svc]}"` reads the CALLER's
  # `svc`, not the one being declared on the same line. The server loop hid it perfectly — its
  # caller-side `svc` happened to be the same value — and it surfaced only on the terminal, which
  # was probed on the runtime's port and refused for "publishing no host port".
  local svc="$1"
  local name="$2"
  local port="${BG_PORT[$svc]}"
  local path="${BG_PATH[$svc]}"
  local hp code
  hp=$(wait_host_port "$name" "$port") || die "$name published no host port within 20s"
  # BOTH sides, always (F74): healthy inside the network is not the same fact as reachable from the
  # host, and for twelve minutes on 2026-09-02 the first was true while the second was not.
  code=$(wait_net "$name" "$port" "$path" "$BG_HEALTH_TIMEOUT") || die "$name never answered $path inside $BG_NETWORK (last: $code)"
  ok "in-network  http://$name:$port$path → $code"
  code=$(wait_host "http://$BG_LOOPBACK:$hp$path" "$BG_HEALTH_TIMEOUT") || die "$name never answered $path from the host on :$hp (last: $code)"
  ok "from host   http://$BG_LOOPBACK:$hp$path → $code"
  if [ "$svc" = terminal ]; then
    assert_terminal_serves "http://$BG_LOOPBACK:$hp/" || die "$name answers on :$hp but does not serve the app shell — a port that answers is not a terminal"
  fi
  echo "$hp"
}

# THE ATOMIC HALF for a server: move the alias.
#
# Docker cannot ADD an alias to a live endpoint, so the proven container is reconnected carrying the
# service alias, and the OLD one is taken off it only AFTER the new one has answered on it. Through
# that overlap the alias resolves to BOTH, both are healthy, and no consumer restarts or changes a
# URL: the terminal proxy, the rig and the flows lanes keep resolving `agent-api`.
#
# The reconnect also re-publishes the host port, and docker may pick a DIFFERENT ephemeral one — so
# the port is re-read afterwards and re-probed rather than assumed. An nginx upstream left pointing
# at the pre-reconnect port is a 502 on the stable entry: the failure this exists to prevent,
# arrived at from the other side.
switch_alias() {  # <service> <new> <old> -> echoes the new container host port AFTER the switch
  local svc="$1"                     # one per line — see prove_new for why
  local new="$2"
  local old="$3"
  local alias="${BG_ALIAS[$svc]}"
  local port="${BG_PORT[$svc]}"
  local code hp
  docker network disconnect "$BG_NETWORK" "$new"
  docker network connect --alias "$alias" --alias "$new" "$BG_NETWORK" "$new"
  code=$(wait_net "$new" "$port" "${BG_PATH[$svc]}" 30) || {
    docker network disconnect "$BG_NETWORK" "$new" || true
    die "$new stopped answering after taking the alias $alias (last: $code) — the alias is back with $old alone, nothing switched"; }
  ok "alias $alias -> $new  $(now)  (post-switch health $code)"
  hp=$(wait_host_port "$new" "$port") || die "$new lost its host port across the reconnect"
  code=$(wait_host "http://$BG_LOOPBACK:$hp${BG_PATH[$svc]}" 30) || die "$new is unreachable from the host on :$hp after the reconnect (last: $code)"
  ok "from host   http://$BG_LOOPBACK:$hp${BG_PATH[$svc]} -> $code"
  echo "$hp"
}

rename_prev() {  # <container> -> echoes the resulting name
  local c="$1"
  case "$c" in *-prev) echo "$c"; return 0 ;; esac
  if exists "${c}-prev"; then docker rm -f "${c}-prev" >/dev/null 2>&1 || true; fi
  docker rename "$c" "${c}-prev"
  echo "${c}-prev"
}

# State is written INCREMENTALLY, per service, the moment that service is serving. A swap that dies
# between the server pair and the terminal must still leave rollback.sh a record of what moved.
record() {  # <svc> <new> <tag> <newport> <oldname> <oldport>
  mkdir -p "$BG_STATE_DIR"
  python3 - "$BG_STATE" "$BG_PROJECT" "$@" <<'PY'
import datetime, json, os, sys
path, project, svc, new, tag, newport, oldname, oldport = sys.argv[1:9]
st = json.load(open(path)) if os.path.exists(path) else {}
svcs = st.get("services", {})
svcs[svc] = {"container": new, "tag": tag, "host_port": newport,
             "prev": {"container": oldname, "host_port": oldport}}
json.dump({"project": project, "generation": st.get("generation", 0) + 1,
           "updated": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
           "services": svcs}, open(path, "w"), indent=2)
PY
}

# ── THE SWAP ────────────────────────────────────────────────────────────────────────────────────
# ORDER, per service, and every step of it was paid for:
#
#   1. start beside + prove BOTH sides         the new container takes no traffic unproven (F20)
#   2. give the new container the alias        in-network consumers resolve it with no restart; the
#                                              old one still answers it, so the name never resolves
#                                              to nothing
#   3. point the nginx upstream at it, reload  host consumers move; both sides now agree
#   4. take the OLD one off the alias and put
#      it straight back on the network         a bare disconnect kills its published host port —
#                                              127 x 502 in the first rehearsal. Reconnected, it
#                                              stays warm and one command from serving, which is
#                                              the entire value of keeping it up for rollback
#   5. rename it `-prev`, record the state     incrementally: a swap that dies between the server
#                                              pair and the terminal must still be rollback-able
declare -A NEW=() NEWPORT=() PREV=()

for svc in agent-api runtime; do
  [ -n "${TAG[$svc]:-}" ] || continue
  hdr "$svc -> ${TAG[$svc]}"
  old=$(current_container "$svc" || true)
  [ -n "$old" ] || die "nothing is currently serving $svc — this script REPLACES a running service, it does not bootstrap one"
  oldport=$(host_port_of "$old" "${BG_PORT[$svc]}" || true)
  say "serving now: $old ($(docker inspect "$old" --format "{{.Config.Image}}"))"
  name=$(start_beside "$svc" "${TAG[$svc]}" "$old"); NEW[$svc]="$name"
  # In the PARENT shell: `start_beside` runs in a command substitution, and a subshell that
  # registers a container for cleanup registers it nowhere.
  PENDING+=("$name")
  say "started beside: $name  $(now)"
  prove_new "$svc" "$name" >/dev/null
  NEWPORT[$svc]=$(switch_alias "$svc" "$name" "$old")
  release_pending "$name"
  nginx_publish "$svc=${NEWPORT[$svc]}"
  drop_alias "$old"
  ok "alias released by $old — still running, still on its own host port"
  PREV[$svc]=$(rename_prev "$old")
  ok "previous kept up as ${PREV[$svc]} for one rollback step"
  record "$svc" "$name" "${TAG[$svc]}" "${NEWPORT[$svc]}" "${PREV[$svc]}" "${oldport:-}"
done

if [ -n "${TAG[terminal]:-}" ]; then
  hdr "terminal -> ${TAG[terminal]} (LAST — the client never leads the server)"
  # GUARD 4 against what is ACTUALLY SERVING, which after the block above is the new agent-api.
  aport="${NEWPORT[agent-api]:-$(state_get "s.get('services',{}).get('agent-api',{}).get('host_port','')")}"
  [ -n "$aport" ] || die "cannot locate the serving agent-api host port — the pairing rule cannot be checked, so the terminal does not move"
  guard_pairing "$(image_for terminal "${TAG[terminal]}")" "http://$BG_LOOPBACK:$aport"
  old=$(current_container terminal || true)
  [ -n "$old" ] || die "nothing is currently serving the terminal"
  oldport=$(host_port_of "$old" "${BG_PORT[terminal]}" || true)
  say "serving now: $old ($(docker inspect "$old" --format "{{.Config.Image}}"))"
  name=$(start_beside terminal "${TAG[terminal]}" "$old"); NEW[terminal]="$name"
  PENDING+=("$name")
  say "started beside: $name  $(now)"
  NEWPORT[terminal]=$(prove_new terminal "$name")
  # The terminal has no in-network alias — nothing inside the network resolves it — so its whole
  # switch is the upstream rewrite plus a reload. nginx finishes in-flight requests on the old
  # worker processes, which is why app.dev.vexa.ai does not drop one.
  nginx_publish "terminal=${NEWPORT[terminal]}"
  release_pending "$name"
  PREV[terminal]=$(rename_prev "$old")
  ok "previous terminal kept up as ${PREV[terminal]} for one rollback step"
  record terminal "$name" "${TAG[terminal]}" "${NEWPORT[terminal]}" "${PREV[terminal]}" "${oldport:-}"
fi

# ── THE STABLE ENTRIES, AFTER ───────────────────────────────────────────────────────────────────
if [ -n "${BG_STABLE_URLS:-}" ]; then
  hdr "stable entries, after"
  for url in $BG_STABLE_URLS; do
    code=$(host_code "$url")
    [[ "$code" =~ ^[23] ]] && ok "$url → $code" || die "$url → $code AFTER the switch — roll back now: $(dirname "$0")/rollback.sh"
  done
fi

# ── PINS + CONVERGENCE ──────────────────────────────────────────────────────────────────────────
hdr "compose pins"
for svc in "${BG_SERVICES[@]}"; do
  [ -n "${TAG[$svc]:-}" ] || continue
  key="${BG_ENV_PIN[$svc]:-}"
  if [ -n "$key" ]; then env_pin "$key" "${TAG[$svc]}"; else say "$svc — no compose pin (not a compose service)"; fi
done
[ -n "${TAG[runtime]:-}" ] && env_pin AGENT_WORKER_IMAGE "${BG_WORKER_IMAGE:-${BG_IMAGE_PREFIX}agent-worker:${TAG[runtime]}}" || true
check_convergence "${PAIRS[@]}"

DONE_OK=1
hdr "DONE $(now)"
for svc in "${BG_SERVICES[@]}"; do
  [ -n "${NEW[$svc]:-}" ] || continue
  say "$svc  ${NEW[$svc]}  host :${NEWPORT[$svc]}  prev ${PREV[$svc]:-} (up)"
done
assert_no_workers_moved "$WORKERS_BEFORE"
say "rollback: $(pwd)/rollback.sh    ·    retire the previous set: $0 --retire"
