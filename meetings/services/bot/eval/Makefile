# Bot-local L4 eval harness — validate the standalone carved bot vs a live Google Meet.
# The bot is treated as a MODULE whose only difference is it's a runnable service: one command in,
# one PASS/FAIL verdict out, with a live eyeball viewer for the human.
.PHONY: run viewer verify help
HERE := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))

help:
	@echo "make run MEETING=<id>   validate the carved bot on https://meet.google.com/<id> (full loop → VERDICT)"
	@echo "make viewer             start ONLY the local viewer (offline; POST /lifecycle /transcript /verdict)"
	@echo "make verify             offline self-test of the scorer (verdict PASS + FAIL fixtures)"

# Full end-to-end run. MEETING is the native meet id (e.g. rvf-kywf-pxb).
run:
	@test -n "$(MEETING)" || { echo "usage: make run MEETING=<meet-id>"; exit 2; }
	@bash "$(HERE)run.sh" "$(MEETING)"

# Just the viewer (for offline UI work / curl-feeding).
viewer:
	@PORT=$${PORT:-8090} node "$(HERE)viewer/server.mjs"

# Offline scorer self-test — no meeting, no bbb. Proves the gate fails on misattr and passes clean.
verify:
	@bash "$(HERE)verify.sh"
