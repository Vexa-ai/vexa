# Local Whisper Healthcheck Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the opt-in Vexa Lite local Whisper container report truthful Docker health without installing `curl` or changing transcription behavior.

**Architecture:** Vexa Lite owns the third-party image adaptation at its `docker run` boundary. An exact `ALLOY_STT_HEALTHCHECK=1` Make switch adds a Python standard-library health command plus the image's existing timing values; flag-off expansion adds no override and therefore preserves upstream behavior.

**Tech Stack:** GNU Make, Docker Engine in WSL2 Ubuntu, Python 3 standard library, `unittest`.

## Global Constraints

- Work only in `F:\vexa-whisper-healthcheck` on branch `alloy/whisper-healthcheck-fix`.
- Do not modify, stage, commit, or clean the dirty primary checkout `F:\vexa`.
- `ALLOY_STT_HEALTHCHECK` defaults to `0`; only exact `1` enables the override; rollback is `0`.
- Mark the downstream Makefile block with `ALLOY:`; any new runtime diagnostic would require `[ALLOY]`.
- Preserve the third-party Whisper image, process command, model, network, port, restart policy, and health timing `5s` interval / `3s` timeout / `30` retries.
- Apply DRY and SOLID proportionately: one Make variable owns the Docker arguments, configuration remains separate from the container process, no derived image or unrelated refactor.
- Run exact bounded tests first. Do not run the full gate suite until the focused RED→GREEN and live health proof are closed.
- Execute inline in this side conversation; subagents are unavailable.

---

### Task 1: Add the opt-in healthcheck adapter

**Files:**
- Create: `deploy/lite/tests/test_local_stt_healthcheck.py`
- Modify: `deploy/lite/Makefile:41-52`
- Modify: `deploy/lite/Makefile:150-157`
- Create: `docs/ALLOY-CUSTOMIZATIONS.md`
- Modify: `deploy/lite/README.md:113-115`
- Modify: `deploy/lite/tests/README.md`

**Interfaces:**
- Consumes: GNU Make command-line/environment variable `ALLOY_STT_HEALTHCHECK`.
- Produces: `ALLOY_STT_HEALTHCHECK_ARGS`, an empty Make variable unless the switch is exactly `1`; when enabled it expands to Docker health options.

- [ ] **Step 1: Write the failing generated-command regression**

Create `deploy/lite/tests/test_local_stt_healthcheck.py`:

```python
from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
LITE_DIR = ROOT / "deploy" / "lite"


def render_up(flag: str | None) -> str:
    args = [
        "make",
        "-s",
        "-n",
        "-C",
        str(LITE_DIR),
        "up",
        "LOCAL_STT=1",
    ]
    if flag is not None:
        args.append(f"ALLOY_STT_HEALTHCHECK={flag}")
    result = subprocess.run(
        args,
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


class LocalSttHealthcheckTest(unittest.TestCase):
    def test_exact_opt_in_uses_python_probe_and_preserves_timing(self) -> None:
        rendered = render_up("1")

        self.assertIn("--health-cmd=", rendered)
        self.assertIn("python3 -c", rendered)
        self.assertIn("urllib.request.urlopen", rendered)
        self.assertIn("--health-interval=5s", rendered)
        self.assertIn("--health-timeout=3s", rendered)
        self.assertIn("--health-retries=30", rendered)

    def test_disabled_switch_preserves_upstream_healthcheck(self) -> None:
        for flag in (None, "", "0", "true"):
            with self.subTest(flag=flag):
                rendered = render_up(flag)

                self.assertNotIn("--health-cmd=", rendered)
                self.assertNotIn("urllib.request.urlopen", rendered)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the regression to verify RED**

Run from PowerShell:

```powershell
wsl.exe -d Ubuntu -- bash -lc 'cd /mnt/f/vexa-whisper-healthcheck && GIT_DIR=/mnt/f/vexa/.git/worktrees/vexa-whisper-healthcheck GIT_WORK_TREE=/mnt/f/vexa-whisper-healthcheck python3 -m unittest discover -s deploy/lite/tests -p "test_local_stt_healthcheck.py" -v'
```

Expected: one failure in `test_exact_opt_in_uses_python_probe_and_preserves_timing` because the generated command contains no `--health-cmd`; the flag-off negative control passes. Stop after 45 seconds. A repeated identical failure is deterministic RED and must not be rerun unchanged.

- [ ] **Step 3: Add the minimal Makefile adapter**

Add beside the existing Local CPU STT variables:

```make
ALLOY_STT_HEALTHCHECK ?= 0

# ALLOY: adapt the optional third-party Whisper image only when explicitly enabled.
# The image's baked probe calls curl, which that image does not contain.
ALLOY_STT_HEALTHCHECK_ARGS :=
ifeq ($(ALLOY_STT_HEALTHCHECK),1)
ALLOY_STT_HEALTHCHECK_ARGS := --health-cmd="python3 -c 'import urllib.request; urllib.request.urlopen(\"http://localhost:8000/health\", timeout=2).read()'" --health-interval=5s --health-timeout=3s --health-retries=30
endif
```

Add the owned argument at the existing Whisper `docker run` boundary immediately before the environment variables:

```make
				$(ALLOY_STT_HEALTHCHECK_ARGS) \
```

Do not change the image, command, environment, port, network, or app-container launch.

- [ ] **Step 4: Run the regression to verify GREEN**

Run the exact Step 2 command.

Expected: `Ran 2 tests` and `OK`, under 45 seconds.

- [ ] **Step 5: Document the switch and test surface**

Create `docs/ALLOY-CUSTOMIZATIONS.md`:

```markdown
# ALLOY customizations

## `ALLOY_STT_HEALTHCHECK`

- Surface: Vexa Lite `LOCAL_STT=1`.
- Default: `0` or unset — retain the third-party Whisper image healthcheck.
- Enabled: `1` — override only the health command with a Python `GET /health` probe while preserving `5s` interval, `3s` timeout, and `30` retries.
- Rollback: set `ALLOY_STT_HEALTHCHECK=0` and recreate `vexa-lite-whisper`.
- Scope: Docker self-health reporting only; the image, model, STT API, and transcription path are unchanged.
```

Add to the make-variable paragraph in `deploy/lite/README.md`:

```markdown
Set `ALLOY_STT_HEALTHCHECK=1` to replace the bundled third-party image's unusable
`curl` self-probe with an equivalent Python probe. Default/rollback `0` leaves the
image healthcheck unchanged.
```

Add to `deploy/lite/tests/README.md`:

```markdown
- `test_local_stt_healthcheck.py` — generated-command regression proving the local
  Whisper Python health override is exact-opt-in and preserves upstream behavior
  when disabled.
```

- [ ] **Step 6: Run the focused affected boundary**

Run:

```powershell
wsl.exe -d Ubuntu -- bash -lc 'cd /mnt/f/vexa-whisper-healthcheck && GIT_DIR=/mnt/f/vexa/.git/worktrees/vexa-whisper-healthcheck GIT_WORK_TREE=/mnt/f/vexa-whisper-healthcheck python3 -m unittest discover -s deploy/lite/tests -p "test_local_stt_healthcheck.py" -v'
node scripts/gates.mjs readme
git diff --check
```

Expected: 2 tests pass, `gate:readme` passes, and `git diff --check` emits no output. Stop each command after 45 seconds; do not broaden on red.

- [ ] **Step 7: Commit the implementation**

```powershell
git add -- deploy/lite/Makefile deploy/lite/tests/test_local_stt_healthcheck.py deploy/lite/tests/README.md deploy/lite/README.md docs/ALLOY-CUSTOMIZATIONS.md
git diff --cached --check
git commit -m "fix(lite): correct local whisper healthcheck"
```

Expected: only the five named files are staged; commit succeeds without attribution trailers.

### Task 2: Prove the correction on the live Lite Whisper container

**Files:**
- Modify: none.

**Interfaces:**
- Consumes: the Task 1 generated Docker health arguments and the existing cached Whisper model.
- Produces: live evidence for direct HTTP health, Docker health, unchanged process/image, and real fixture transcription.

- [ ] **Step 1: Record the pre-change runtime anchors**

Run read-only:

```powershell
wsl.exe -d Ubuntu -- docker inspect --format '{{.Image}}|{{json .Config.Cmd}}|{{json .Config.Healthcheck}}|{{.HostConfig.NetworkMode}}|{{.RestartCount}}' vexa-lite-whisper
wsl.exe -d Ubuntu -- docker inspect --format '{{.State.StartedAt}}|{{.RestartCount}}' vexa-lite
```

Expected: Whisper uses the existing faster-whisper image and `uv run uvicorn`; app restart count remains `0`. Save the exact outputs for the final evidence.

- [ ] **Step 2: Recreate only `vexa-lite-whisper` with the enabled probe**

Run from PowerShell:

```powershell
$whisperRecreateScript = @'
set -euo pipefail
whisper_name=vexa-lite-whisper
actual_name="$(docker inspect -f '{{.Name}}' "$whisper_name")"
[ "$actual_name" = "/$whisper_name" ]

whisper_image="$(docker inspect -f '{{.Config.Image}}' "$whisper_name")"
whisper_network="$(docker inspect -f '{{.HostConfig.NetworkMode}}' "$whisper_name")"
whisper_port="$(docker inspect -f '{{(index (index .HostConfig.PortBindings "8000/tcp") 0).HostPort}}' "$whisper_name")"
whisper_model="$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$whisper_name" | sed -n 's/^WHISPER__MODEL=//p')"

[ -n "$whisper_image" ]
[ -n "$whisper_network" ]
[ "$whisper_port" = "8083" ]
[ -n "$whisper_model" ]

docker rm -f "$whisper_name"
docker run -d \
  --name "$whisper_name" \
  --network "$whisper_network" \
  --restart unless-stopped \
  --health-cmd="python3 -c 'import urllib.request; urllib.request.urlopen(\"http://localhost:8000/health\", timeout=2).read()'" \
  --health-interval=5s \
  --health-timeout=3s \
  --health-retries=30 \
  -e WHISPER__MODEL="$whisper_model" \
  -e WHISPER__INFERENCE_DEVICE=cpu \
  -e WHISPER__TTL=-1 \
  -p "$whisper_port":8000 \
  "$whisper_image"
'@
$whisperRecreateScript | wsl.exe -d Ubuntu -- bash
```

Expected: validation completes before removal, then only `vexa-lite-whisper` is recreated. The inspected image, model, network, `8083:8000` binding, and restart policy are preserved. `vexa-lite`, PostgreSQL, and MinIO are not restarted or recreated.

- [ ] **Step 3: Wait on observable conditions**

Poll for at most 180 seconds:

```powershell
$healthDeadline = (Get-Date).AddSeconds(180)
$lastHttpStatus = $null
$lastDockerHealth = ""

do {
    try {
        $healthResponse = Invoke-WebRequest -UseBasicParsing http://localhost:8083/health -TimeoutSec 5
        $lastHttpStatus = $healthResponse.StatusCode
    } catch {
        $lastHttpStatus = $null
    }

    $lastDockerHealth = (
        wsl.exe -d Ubuntu -- docker inspect --format '{{.State.Health.Status}}' vexa-lite-whisper
    ).Trim()

    if ($lastHttpStatus -eq 200 -and $lastDockerHealth -eq "healthy") {
        break
    }
    if ($lastDockerHealth -eq "unhealthy") {
        wsl.exe -d Ubuntu -- docker inspect --format '{{json .State.Health.Log}}' vexa-lite-whisper
        throw "Whisper healthcheck became unhealthy"
    }
    Start-Sleep -Seconds 5
} while ((Get-Date) -lt $healthDeadline)

if ($lastHttpStatus -ne 200 -or $lastDockerHealth -ne "healthy") {
    throw "Whisper did not become HTTP 200 + Docker healthy within 180 seconds"
}
```

Expected: direct HTTP status `200`, then Docker health `healthy`. Stop immediately on `unhealthy` with a Python exception in the health log, or at 180 seconds; do not rerun unchanged.

- [ ] **Step 4: Prove the container identity and transcription path**

Run:

```powershell
wsl.exe -d Ubuntu -- docker inspect --format '{{.Image}}|{{json .Config.Cmd}}|{{json .Config.Healthcheck}}|{{.RestartCount}}' vexa-lite-whisper
wsl.exe -d Ubuntu -- bash -lc 'cd /mnt/f/vexa-whisper-healthcheck && GIT_DIR=/mnt/f/vexa/.git/worktrees/vexa-whisper-healthcheck GIT_WORK_TREE=/mnt/f/vexa-whisper-healthcheck make -C deploy/lite stt-smoke'
wsl.exe -d Ubuntu -- docker inspect --format '{{.State.StartedAt}}|{{.RestartCount}}' vexa-lite
```

Expected: image ID and `uv run uvicorn` command match Step 1; health test uses Python with `5s/3s/30`; `stt-smoke` reports expected words; `vexa-lite` start timestamp and restart count are unchanged. Stop `stt-smoke` after 120 seconds.

- [ ] **Step 5: Final repository verification**

Run:

```powershell
git status --short
git log -2 --oneline
git -C F:\vexa status --short
```

Expected: isolated worktree is clean with the design and implementation commits; primary `F:\vexa` remains at its original 24 tracked modifications, 19 untracked paths, and zero staged files.
