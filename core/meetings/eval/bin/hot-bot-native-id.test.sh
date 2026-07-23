#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"

check() {
  local url=$1 expected=$2
  local actual
  actual=$("$HERE/hot-bot.sh" --derive-native zoom "$url")
  [ "$actual" = "$expected" ] || {
    echo "FAIL $url: expected $expected, got $actual" >&2
    exit 1
  }
}

check "https://app.zoom.us/j/2923712604?pwd=secret"                      "2923712604"
check "https://app.zoom.us/w/2923712604?pwd=secret"                      "2923712604"
check "https://app.zoom.us/wc/2923712604/join?pwd=&tk=&fromPWA=1"        "2923712604"
check "https://app.zoom.us/wc/join/2923712604?pwd=secret"                "2923712604"

echo "PASS hot-bot Zoom native-id parsing"
