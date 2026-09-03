#!/usr/bin/env bash
# bg-lib.sh — the shared half of the blue/green deploy: configuration, guards, probes, state.
# Sourced by deploy.sh and rollback.sh. Not executable on its own.
#
# ── THE STANDING RULE FOR THIS BOX (founder/coordinator, 2026-09-02) ────────────────────────────
#   never `docker system prune`, never stop a container you did not create, never kill a process
#   holding a port — if you cannot proceed, stop and surface it. A "port already allocated" is
#   evidence that another stack owns the port, never of a leak.
#
# On 2026-09-02 that rule was broken here (F74): two docker-proxy processes were killed as
# "orphans"; they were the LIVE dogfood host bindings, and the flows lane had no path to agent-api
# for twelve minutes while every container reported healthy. Health inside a network says nothing
# about reachability from the host, which is why every wait below probes BOTH.
#
# EVERY action in this library that changes anything, in full:
#   docker run          — creates a NEW container, under a name containing the tag being deployed
#   docker network connect / disconnect — moves a service ALIAS between containers
#   docker rename       — the outgoing container becomes <name>-prev, still running
#   docker rm -f        — ONLY a container this run created and failed to bring up, or a `-prev`
#                         container being retired by an explicit `--retire`
#   writes              — the nginx upstreams include, the compose .env pins (with a .bak), and
#                         the state file
# There is no `kill`, no `pkill`, no `docker stop`, no `docker restart`, no prune, anywhere.
#
# Workers (`vexa-worker-*`) are NEVER touched: they are per-dispatch containers and a turn in
# flight finishes inside the one it started in (PRD decision 39, item 4). `assert_no_workers_moved`
# proves it rather than promising it.

set -euo pipefail

# ── CONFIGURATION ───────────────────────────────────────────────────────────────────────────────
# Every value is overridable so the whole thing can be rehearsed against a throwaway project. The
# DEFAULTS are the founder's live stack, and the project is ASSERTED before anything runs — F68 was
# a swap that recreated `vexa-v012-*` while reporting success, because both compose files on this
# box declare `name: vexa-v012` on line 1 and the dogfood stack is a `-p` override.
BG_PROJECT="${BG_PROJECT:-vexa-dogfood}"
BG_COMPOSE_DIR="${BG_COMPOSE_DIR:-$HOME/dev/estate/deploy/compose}"
BG_NETWORK="${BG_NETWORK:-${BG_PROJECT}_vexa}"
BG_IMAGE_PREFIX="${BG_IMAGE_PREFIX:-vexaai/v012-}"
BG_TERMINAL_CONTAINER="${BG_TERMINAL_CONTAINER:-vexa-minutes-terminal}"
BG_TERMINAL_SUFFIX="${BG_TERMINAL_SUFFIX:--minutes}"
BG_STATE_DIR="${BG_STATE_DIR:-$HOME/.vexa-bluegreen/$BG_PROJECT}"
BG_STATE="$BG_STATE_DIR/state.json"
BG_ENV_FILE="${BG_ENV_FILE:-$BG_COMPOSE_DIR/.env}"
BG_ENV_FILES="${BG_ENV_FILES:-}"          # extra --env-file args for `docker compose`, space-separated
BG_NGINX_UPSTREAMS="${BG_NGINX_UPSTREAMS:-/etc/nginx/conf.d/vexa-dogfood-upstreams.conf}"
BG_NGINX_TEST="${BG_NGINX_TEST:-sudo nginx -t}"
# HOW THE HOST-SIDE SWITCH IS APPLIED. A graceful `nginx -s reload` is the right answer everywhere
# this scheme was rehearsed, and it is why the switch is documented as costing zero requests: nginx
# finishes in-flight requests on the old worker generation.
#
# ON THIS HOST IT IS NOT (2026-09-03). Two reloads took the whole edge down — every :443 vhost plus
# the stable ports — for three minutes each. Both recovered instantly on `systemctl restart`. After
# a reload the new generation comes up with ONE live worker instead of 128 (`worker_processes auto`
# on a 128-core box); the old generation drains but the new one never fills, so nginx stops
# accepting. NOTHING IS LOGGED: `nginx -t` passes, the reload reports success, and neither
# error.log nor the journal records a word about the new workers. The only entry is systemd
# SIGKILLing the old generation when the later restart's stop phase times out.
#
# `worker_shutdown_timeout 60s` was added and DOES work — the old generation collapses inside 35s —
# and it changed nothing about the new one, which is what rules the SSE-draining theory out.
# Unconfirmed leading suspect: nchan, the only third-party module here, holding a 128MB
# shared-memory zone that both generations must map across a reload.
#
# So on this deployment the switch is a RESTART. The founder accepts a sub-second blip on a swap
# (decision 39.5), and a deterministic blip beats a three-minute outage nobody can predict. Still
# one knob, overridable: a deployment whose reload works sets BG_NGINX_RELOAD back to it.
BG_NGINX_RELOAD="${BG_NGINX_RELOAD:-sudo systemctl restart nginx}"
BG_NGINX_WRITE="${BG_NGINX_WRITE:-sudo tee}"   # how the upstreams file is written (tee reads stdin)
BG_CURL_IMAGE="${BG_CURL_IMAGE:-curlimages/curl:8.12.1}"
BG_HEALTH_TIMEOUT="${BG_HEALTH_TIMEOUT:-90}"
BG_LOOPBACK="${BG_LOOPBACK:-127.0.0.1}"

# service → container port · health path · in-network alias · nginx upstream name
declare -A BG_PORT=(       [agent-api]=8100        [runtime]=8090        [terminal]=3000 )
declare -A BG_PATH=(       [agent-api]=/health     [runtime]=/health     [terminal]=/ )
declare -A BG_ALIAS=(      [agent-api]=agent-api   [runtime]=runtime     [terminal]= )
declare -A BG_UPSTREAM=(   [agent-api]=vexa_agent_api [runtime]=vexa_runtime [terminal]=vexa_app )
# The compose variable that pins each service's image, so a later plain `up -d` CONVERGES on what
# was deployed instead of silently rolling it back (the override file's own lesson, 2026-09-02:
# a bare `up -d` reverted meeting-api and took two fixes with it, and nothing errored).
declare -A BG_ENV_PIN=(    [agent-api]=AGENT_API_IMAGE_TAG [runtime]=RUNTIME_IMAGE_TAG [terminal]= )

BG_SERVICES=(agent-api runtime terminal)

# EVERY diagnostic goes to STDERR. Functions in this file return values on stdout — a host port, a
# container name — and a `say` that shared the channel would be captured into the caller's
# variable. That is not a style preference: it happened on the first rehearsal run, the port
# variable came back holding three lines of log, and the pairing guard then probed a URL with a
# newline in it and refused a terminal for the wrong reason.
say()  { printf '  %s\n' "$*" >&2; }
ok()   { printf '  ✓ %s\n' "$*" >&2; }
warn() { printf '  ⚠ %s\n' "$*" >&2; }
die()  { printf '  REFUSE — %s\n' "$*" >&2; exit 1; }
hdr()  { printf '\n── %s %s\n' "$*" "$(printf '%.0s─' $(seq 1 $((70 - ${#1}))))" >&2; }
now()  { date -u +%H:%M:%SZ; }

# ── IMAGE NAMES ─────────────────────────────────────────────────────────────────────────────────
image_for() {  # <service> <tag>
  case "$1" in
    terminal) echo "${BG_IMAGE_PREFIX}terminal:$2${BG_TERMINAL_SUFFIX}" ;;
    *)        echo "${BG_IMAGE_PREFIX}$1:$2" ;;
  esac
}
container_for() { echo "${BG_PROJECT}-$1-$2"; }   # <service> <tag> — the versioned name

# ── DOCKER READS ────────────────────────────────────────────────────────────────────────────────
exists()  { docker inspect "$1" >/dev/null 2>&1; }
running() { [ "$(docker inspect -f '{{.State.Running}}' "$1" 2>/dev/null || echo false)" = true ]; }


# Which container currently answers to a network alias. This is the authoritative read of "what is
# serving" — not the container name, not the compose service, not a tag someone typed.
holder_of_alias() {  # <alias>
  local alias="$1" c
  for c in $(docker network inspect "$BG_NETWORK" --format '{{range .Containers}}{{.Name}}
{{end}}' 2>/dev/null); do
    if docker inspect "$c" --format "{{range \$n, \$v := .NetworkSettings.Networks}}{{range \$v.Aliases}}{{println .}}{{end}}{{end}}" 2>/dev/null \
         | grep -qx "$alias"; then echo "$c"; return 0; fi
  done
  return 1
}

# The container-level env ONLY: everything in the container's env minus everything the IMAGE baked.
#
# This is the fix for the way `/tmp/swap.sh` carried the terminal's env across a swap — it copied
# the whole of `.Config.Env`, which is image defaults AND `-e` flags fused together. Copy that onto
# a new image and the OLD image's baked defaults silently outrank the new one's. F67 is exactly
# that failure one level up (a variant that was a property of the bundle and not of the image), and
# the terminal's mode is a baked NEXT_PUBLIC_* — so replaying it would pin a new terminal to the
# old build's mode without anyone typing it.
container_env() {  # <container>
  local c="$1" img
  img=$(docker inspect "$c" --format '{{.Config.Image}}')
  local imgenv; imgenv=$(docker image inspect "$img" --format '{{range .Config.Env}}{{println .}}{{end}}' 2>/dev/null || true)
  docker inspect "$c" --format '{{range .Config.Env}}{{println .}}{{end}}' \
    | grep -v '^$' \
    | while IFS= read -r line; do grep -qxF "$line" <<<"$imgenv" || printf '%s\n' "$line"; done
}

# Bind mounts + named volumes of a container, as `docker run` flags.
container_mounts() {  # <container>
  docker inspect "$1" --format '{{range .Mounts}}{{if eq .Type "bind"}}-v {{.Source}}:{{.Destination}}{{if not .RW}}:ro{{end}}
{{else if eq .Type "volume"}}-v {{.Name}}:{{.Destination}}{{if not .RW}}:ro{{end}}
{{end}}{{end}}'
}

host_port_of() { docker port "$1" "$2/tcp" 2>/dev/null | head -1 | sed 's/.*://'; }

# The published port of a container that has just been started, or just been reconnected. It is a
# WAIT and not a read because the mapping is populated asynchronously: on the rehearsal rig the
# server pair answered `docker port` immediately and the terminal, started one second later, did
# not — and a swap that concluded "this container published no host port" from a race would refuse
# a perfectly good deploy about a quarter of the time.
wait_host_port() {  # <container> <container-port> [timeout-s]
  local c="$1" p="$2" t="${3:-20}" i hp
  for ((i = 0; i < t * 4; i++)); do
    hp=$(host_port_of "$c" "$p")
    if [ -n "$hp" ] && [ "$hp" != 0 ]; then echo "$hp"; return 0; fi
    sleep 0.25
  done
  return 1
}

# What is serving a service RIGHT NOW: the ALIAS HOLDER for a server — authoritative, and not the
# container name, not the compose service, not a tag someone typed — then the state file, then the
# well-known name for the terminal, which has no in-network alias.
current_container() {  # <service>
  local svc="$1" c
  if [ -n "${BG_ALIAS[$svc]:-}" ]; then
    c=$(holder_of_alias "${BG_ALIAS[$svc]}" || true)
    [ -n "$c" ] && { echo "$c"; return 0; }
  fi
  c=$(state_get "s.get('services',{}).get('$svc',{}).get('container','')")
  if [ -n "$c" ] && exists "$c"; then echo "$c"; return 0; fi
  if [ "$svc" = terminal ] && exists "$BG_TERMINAL_CONTAINER"; then echo "$BG_TERMINAL_CONTAINER"; return 0; fi
  return 1
}

# ── PROBES — BOTH SIDES, ALWAYS ─────────────────────────────────────────────────────────────────
# F74 in one line: every container was healthy inside its network and nothing on the host could
# reach any of them for twelve minutes. A single-sided check would have reported that as green.
host_code() {  # <url>
  curl -sS -m 4 -o /dev/null -w '%{http_code}' "$1" 2>/dev/null || echo 000
}

wait_host() {  # <url> <timeout-s> — 2xx/3xx
  local url="$1" t="${2:-$BG_HEALTH_TIMEOUT}" i code
  for ((i = 0; i < t; i++)); do
    code=$(host_code "$url")
    [[ "$code" =~ ^[23] ]] && { echo "$code"; return 0; }
    sleep 1
  done
  echo "$code"; return 1
}

# Inside the compose network, resolving the target the way its real consumers do — by DNS name.
# One short-lived curl container runs the whole retry loop, so a wait costs one `docker run`.
wait_net() {  # <dns-name> <port> <path> <timeout-s>
  local name="$1" port="$2" path="$3" t="${4:-$BG_HEALTH_TIMEOUT}"
  docker run --rm --network "$BG_NETWORK" "$BG_CURL_IMAGE" \
    sh -c "for i in \$(seq 1 $t); do c=\$(curl -sS -m 3 -o /dev/null -w '%{http_code}' http://$name:$port$path 2>/dev/null || echo 000); case \$c in 2*|3*) echo \$c; exit 0;; esac; sleep 1; done; echo \$c; exit 1" 2>/dev/null
}

net_code() {  # <dns-name> <port> <path> — one shot, no retry
  wait_net "$1" "$2" "$3" 1
}

# The terminal answers 404 on /health (no such route — swap.sh learned this the hard way), so its
# check is `/`. And a 200 alone only proves a port answered: assert the app SHELL actually rendered.
assert_terminal_serves() {  # <url>
  local url="$1" body; body=$(mktemp)
  local code; code=$(curl -sS -m 10 -o "$body" -w '%{http_code}' "$url" 2>/dev/null || echo 000)
  local bytes; bytes=$(wc -c < "$body")
  if [ "$code" = 200 ] && grep -qF '<title>Vexa Terminal</title>' "$body" && grep -qF '_next/static' "$body"; then
    rm -f "$body"; say "$url  $code  bytes=$bytes  app shell RENDERED"; return 0
  fi
  rm -f "$body"; say "$url  $code  bytes=$bytes"; return 1
}

# ── GUARDS ──────────────────────────────────────────────────────────────────────────────────────
# G1 — THE TARGET. Assert the project resolves to the intended containers before any command runs.
guard_project() {
  local ps
  ps=$(compose ps --format '{{.Name}}' 2>/dev/null || true)
  [ -n "$ps" ] || die "'docker compose -p $BG_PROJECT ps' in $BG_COMPOSE_DIR lists nothing — wrong project or wrong directory"
  grep -q "^${BG_PROJECT}-" <<<"$ps" || die "compose project resolves to something other than ${BG_PROJECT}-*: $(tr '\n' ' ' <<<"$ps")"
  ok "GUARD 1 — target is $BG_PROJECT ($(grep -c . <<<"$ps") compose containers) in $BG_COMPOSE_DIR"
}

# G2 — THE IMAGES. Every image this deploy would run must already be on this host. A missing image
# turns a start into a pull (slow, network-dependent) or a dead container.
guard_images() {  # <svc>=<tag>...
  local pair svc tag img
  for pair in "$@"; do
    svc="${pair%%=*}"; tag="${pair#*=}"; img=$(image_for "$svc" "$tag")
    docker image inspect "$img" >/dev/null 2>&1 || die "image for '$svc' is not on this host: $img"
    ok "GUARD 2 — $svc → $img (present)"
  done
}

# G3 — THE TERMINAL VARIANT (F67). The minutes and default terminals come out of one Dockerfile and
# differ only by a build arg: both build, both pass every test, both start, and the `-minutes` in a
# tag is a label the builder typed. Read the LABEL; fall back to the baked build-arg env.
terminal_variant() {  # <image>
  local v; v=$(docker image inspect "$1" --format '{{index .Config.Labels "ai.vexa.terminal.mode"}}' 2>/dev/null || true)
  [ -n "$v" ] && [ "$v" != "<no value>" ] && { echo "label:$v"; return; }
  echo "env:$(docker image inspect "$1" --format '{{range .Config.Env}}{{println .}}{{end}}' 2>/dev/null \
        | sed -n 's/^NEXT_PUBLIC_TERMINAL_MODE=//p' | head -1 | grep . || echo default)"
}

guard_terminal_variant() {  # <image>
  local v; v=$(terminal_variant "$1")
  case "$v" in
    label:minutes|env:minutes) ok "GUARD 3 — $1 is the minutes variant ($v)" ;;
    *) die "$1 is NOT the minutes variant ($v). Rebuild with --build-arg NEXT_PUBLIC_TERMINAL_MODE=minutes" ;;
  esac
}

# G4 — THE PAIRING RULE (F55/F77). The terminal and the server must move together, and both
# directions have already cost a window:
#   F55  a NEW terminal on an OLD server — the scaffold's note tab pointed at a path that server
#        never writes.
#   F77  a new terminal shipped a button whose request every running agent-api rejected with 422.
# So: the terminal image declares the agent-api contract it was built against, and this asks the
# agent-api that is ACTUALLY SERVING what it answers. Not what was deployed, not what a tag says.
guard_pairing() {  # <terminal-image> <agent-api-base-url>
  local img="$1" base="$2" want got body
  want=$(docker image inspect "$img" --format '{{index .Config.Labels "ai.vexa.terminal.agent_api"}}' 2>/dev/null || true)
  [ -n "$want" ] && [ "$want" != "<no value>" ] \
    || die "terminal image $img declares no ai.vexa.terminal.agent_api label — it predates the pairing rule and cannot be paired. Rebuild it."
  body=$(curl -sS -m 5 "$base/api/version" 2>/dev/null || true)
  got=$(printf '%s' "$body" | python3 -c 'import json,sys
try: print(json.load(sys.stdin).get("api",""))
except Exception: print("")' 2>/dev/null || true)
  [ -n "$got" ] || die "the agent-api serving at $base does not answer GET /api/version — it cannot be paired with a terminal. Deploy an agent-api that carries the endpoint first."
  [ "$want" = "$got" ] || die "PAIRING BREAK — terminal $img expects agent-api contract api=$want; the agent-api serving at $base answers api=$got. This is F55/F77: putting that terminal in front of a person ships a client the server rejects."
  ok "GUARD 4 — pairing holds: terminal expects api=$want, serving agent-api answers api=$got"
}

# G5 — THE HOST PORTS. Asserted before and after, because "healthy" is not "reachable" (F74).
guard_host_ports() {  # <url>...
  local url code
  for url in "$@"; do
    code=$(host_code "$url")
    [[ "$code" =~ ^[23] ]] || die "host check failed BEFORE any change: $url → $code. Fix the stack first; a blue/green swap is not a repair tool."
    ok "GUARD 5 — $url → $code"
  done
}

# ── WORKERS ARE NEVER TOUCHED ───────────────────────────────────────────────────────────────────
worker_set() { docker ps --format '{{.Names}}' | grep '^vexa-worker-' | sort || true; }
assert_no_workers_moved() {  # <before-snapshot>
  local after; after=$(worker_set)
  if [ "$1" != "$after" ]; then
    warn "the set of running vexa-worker-* containers changed during this run:"
    diff <(printf '%s\n' "$1") <(printf '%s\n' "$after") | sed 's/^/      /' || true
    warn "workers are per-dispatch — they start and finish on their own — but nothing here may move one."
  else
    ok "workers untouched ($(grep -c . <<<"$1" 2>/dev/null || echo 0) running, same set)"
  fi
}

# ── COMPOSE ─────────────────────────────────────────────────────────────────────────────────────
compose() {
  local ef=() f
  for f in $BG_ENV_FILES; do ef+=(--env-file "$f"); done
  (cd "$BG_COMPOSE_DIR" && docker compose -p "$BG_PROJECT" "${ef[@]}" "$@")
}

# ── NGINX — THE HOST-SIDE SWITCH ────────────────────────────────────────────────────────────────
# One generated include holds one `upstream` per service; the vhosts proxy_pass to those names. A
# swap rewrites the file and reloads: nginx finishes in-flight requests on the old worker processes
# and starts new ones on the new upstream, which is why the switch costs zero requests. Rollback is
# the same write with the previous ports.
nginx_write() {  # reads "name port" lines on stdin
  local tmp; tmp=$(mktemp)
  {
    echo "# GENERATED by deploy/dogfood/bin/deploy.sh at $(date -u +%FT%TZ) — do not edit by hand."
    echo "# Each upstream points at the host port of the container currently serving that service."
    echo "# Rewritten and reloaded on every blue/green swap; rollback.sh writes the previous set."
    while read -r name port; do
      [ -n "${name:-}" ] || continue
      printf 'upstream %s { server %s:%s; }\n' "$name" "$BG_LOOPBACK" "$port"
    done
  } > "$tmp"
  $BG_NGINX_WRITE "$BG_NGINX_UPSTREAMS" < "$tmp" >/dev/null
  rm -f "$tmp"
}

nginx_reload() {
  $BG_NGINX_TEST >/dev/null 2>&1 || { $BG_NGINX_TEST || true
    die "nginx -t failed on the generated upstreams — NOT reloaded, so every host-side request is still
      going exactly where it was going before this run. If a service alias was already switched, the new
      and the old container are BOTH on it and both healthy; nothing is dropping requests. Fix the
      upstreams file, re-run, or roll back."; }
  $BG_NGINX_RELOAD
  ok "nginx reloaded $(now)"
}

guard_nginx_adopted() {
  [ -f "$BG_NGINX_UPSTREAMS" ] || die "$BG_NGINX_UPSTREAMS does not exist — the host is not adopted for blue/green.
      The terminal's stable entry (app.dev.vexa.ai) must be an nginx upstream this script can rewrite,
      not a host port bound by the container itself: a container holding :15401 cannot be replaced
      without first being stopped, which is the downtime decision 39 exists to remove.
      Adoption is a ONE-TIME step, see deploy/dogfood/nginx/README.md."
}

# ── STATE ───────────────────────────────────────────────────────────────────────────────────────
state_read() { [ -f "$BG_STATE" ] && cat "$BG_STATE" || echo '{}'; }

state_get() {  # <python-expression over `s`>
  state_read | python3 -c "import json,sys
s=json.load(sys.stdin)
print($1)" 2>/dev/null || true
}

state_write() {  # reads JSON on stdin
  mkdir -p "$BG_STATE_DIR"
  python3 -c 'import json,sys; json.dump(json.load(sys.stdin), open(sys.argv[1],"w"), indent=2)' "$BG_STATE" <&0
}

# ── .env PINS ───────────────────────────────────────────────────────────────────────────────────
# So that a later plain `docker compose up -d` converges on what is deployed rather than silently
# rolling it back. That silent rollback is not hypothetical: on 2026-09-02 a bare `up -d` reverted
# meeting-api and gateway to an older tag and took two live fixes with them, and nothing errored,
# because a container coming up on an older image is not a failure.
env_pin() {  # <KEY> <VALUE>
  local key="$1" val="$2"
  [ -f "$BG_ENV_FILE" ] || { warn "no env file at $BG_ENV_FILE — pin $key skipped"; return 0; }
  cp -n "$BG_ENV_FILE" "$BG_ENV_FILE.bak-bluegreen-$(date -u +%Y%m%dT%H%M%SZ)" 2>/dev/null || true
  if grep -q "^${key}=" "$BG_ENV_FILE"; then
    python3 - "$BG_ENV_FILE" "$key" "$val" <<'PY'
import sys
path, key, val = sys.argv[1:4]
lines = open(path).read().splitlines(keepends=True)
out = []
for line in lines:
    out.append(f"{key}={val}\n" if line.startswith(key + "=") else line)
open(path, "w").writelines(out)
PY
  else
    printf '%s=%s\n' "$key" "$val" >> "$BG_ENV_FILE"
  fi
  ok "pinned $key=$val in $BG_ENV_FILE"
}

# After the pins are written, ASK COMPOSE what it now resolves. A pin that does not actually move
# the service is worse than no pin — it reads as convergence and is not.
check_convergence() {  # <svc>=<tag>...
  local pair svc tag img resolved bad=0
  resolved=$(compose config --format json 2>/dev/null | python3 -c 'import json,sys
d=json.load(sys.stdin).get("services",{})
for n,s in d.items(): print(n, s.get("image",""))' 2>/dev/null || true)
  [ -n "$resolved" ] || { warn "could not ask compose what it resolves — convergence unchecked"; return 0; }
  for pair in "$@"; do
    svc="${pair%%=*}"; tag="${pair#*=}"; img=$(image_for "$svc" "$tag")
    grep -q "^$svc " <<<"$resolved" || continue      # not a compose service (the minutes terminal)
    if grep -qx "$svc $img" <<<"$resolved"; then ok "converge — a plain \`up -d\` would keep $svc on $img"
    else warn "converge — compose resolves $svc to '$(grep "^$svc " <<<"$resolved" | cut -d' ' -f2-)', NOT $img.
      A later bare \`docker compose up -d\` would ROLL THIS SERVICE BACK, silently. Add the pin to
      docker-compose.override.yml (see its header) before relying on convergence."; bad=1; fi
  done
  return 0
}

# Rewrite the upstreams include — the given `<svc>=<port>` overrides, everything else from the
# state file — and reload. This is the HOST-side switch, and it is deliberately a separate step
# from the alias switch, because a service is reached two different ways and only one of them is
# docker's DNS. Keeping them in one function would hide the ordering that matters (see deploy.sh).
nginx_publish() {  # <svc>=<port>...
  local -A over=(); local p svc up port c
  for p in "$@"; do over["${p%%=*}"]="${p#*=}"; done
  {
    for svc in "${BG_SERVICES[@]}"; do
      up="${BG_UPSTREAM[$svc]}"; [ -n "$up" ] || continue
      port="${over[$svc]:-}"
      [ -n "$port" ] || port=$(state_get "s.get('services',{}).get('$svc',{}).get('host_port','')")
      if [ -z "$port" ]; then c=$(current_container "$svc" || true); [ -n "$c" ] && port=$(host_port_of "$c" "${BG_PORT[$svc]}" || true); fi
      [ -n "$port" ] && echo "$up $port" || warn "no host port for '$svc' — its upstream is omitted, and nginx -t will refuse the file if a server block references it"
    done
  } | nginx_write
  sed 's/^/      /' <"$BG_NGINX_UPSTREAMS" >&2
  nginx_reload
}

# Take a container OFF a service alias without taking it out of service.
#
# Found on the first rehearsal, and it is the sharpest edge in this whole mechanism: a plain
# `docker network disconnect` on the outgoing container ALSO kills its published host port — the
# container is left on no network at all, docker tears down its proxy, and every request to its
# stable port answers 502. The rehearsal's own request loop caught 127 of them. So the outgoing
# container is immediately reconnected under its own name only: it keeps running, keeps its host
# binding, and is one `docker network connect` away from serving again, which is the entire value
# of keeping it up for rollback.
drop_alias() {  # <container>
  docker network disconnect "$BG_NETWORK" "$1"
  docker network connect --alias "$1" "$BG_NETWORK" "$1"
}
