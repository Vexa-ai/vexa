#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/fetch_ami.sh"
meetings=$'IS1008a\nIS1008b\nES2011a\nIB4001\nTS3004a'
expected=$'IS1008a\nES2011a\nIB4001'
actual=$(pick_meetings 3 <<< "$meetings")
[[ "$actual" == "$expected" ]]
[[ "$(pick_meetings <<< "$meetings")" == "$meetings" ]]
[[ "$(pick_meetings 9 <<< "$meetings")" == $'IS1008a\nES2011a\nIB4001\nTS3004a' ]]
if pick_meetings 0 <<< "$meetings" 2>/dev/null; then exit 1; fi
printf '%s\n' "$actual"
echo 'ok AMI distinct-group selection'
