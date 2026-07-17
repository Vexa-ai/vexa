"""`.env.example` must be a VALID `docker run --env-file`, and Lite's `up` must not splat it.

Two shipped defects, both found by the v0.12.5 release witness and neither caught by any rung below
it:

1. `docker run --env-file` does NOT strip an inline comment — `FOO=bar  # why` yields the literal
   value `"bar  # why"`. `.env.example` carried five such lines, so a Lite box booted with
   commented-out ports as values.
2. Lite's `up` recipe read `ADMIN_TOKEN` with `cut -d= -f2-` (comment included) and interpolated it
   UNQUOTED into `docker run -e ADMIN_API_TOKEN=$ADMIN_TK`. /bin/sh word-split it, a bare `#` landed
   in the image slot, and `make -C deploy/lite up` died with `docker: invalid reference format`.
   That is what forced the witness box to be hand-built, which is what exposed defect (1) of
   env_flags and cost the release a cycle.
"""
from __future__ import annotations

import re
import shlex
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
ENV_EXAMPLE = ROOT / "deploy" / "compose" / ".env.example"
LITE_MAKEFILE = ROOT / "deploy" / "lite" / "Makefile"

# KEY=value followed by whitespace then a `#`. Docker keeps the comment as part of the value.
INLINE_COMMENT = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(?!\s*$).*?\s+#")


def _env_lines(path: Path):
    for i, line in enumerate(path.read_text().splitlines(), 1):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        yield i, line


def test_env_example_has_no_inline_comments():
    offenders = [
        f"{ENV_EXAMPLE.name}:{i}: {line}"
        for i, line in _env_lines(ENV_EXAMPLE)
        if INLINE_COMMENT.match(line)
    ]
    assert not offenders, (
        "docker's --env-file parser keeps an inline comment as part of the value. "
        "Put the comment on its own line above the key.\n" + "\n".join(offenders)
    )


def test_env_example_values_round_trip_through_a_shell_word_split():
    """Every value must survive unquoted expansion as a single argv word.

    This is the actual failure: `-e K=$V` under /bin/sh. If a value word-splits, extra argv words
    reach `docker run` and the first bare one is taken as the IMAGE.
    """
    bad = []
    for i, line in _env_lines(ENV_EXAMPLE):
        key, _, value = line.partition("=")
        if not value:
            continue
        words = shlex.split(f"K={value}", posix=True) if "'" not in value and '"' not in value else ["ok"]
        if len(words) > 1:
            bad.append(f"{ENV_EXAMPLE.name}:{i}: {key} splits into {len(words)} argv words: {words}")
    assert not bad, "\n".join(bad)


def test_lite_up_strips_inline_comments_and_quotes_interpolations():
    mk = LITE_MAKEFILE.read_text()
    assert "envv()" in mk, "the `up` recipe must read .env through the comment-stripping `envv` helper"
    assert "s/[[:space:]]+#.*$$//" in mk, "`envv` must strip inline comments"
    for var in ("$$ADMIN_TK", "$$TX_URL", "$$TX_TOKEN", "$$IMG"):
        assert f'"{var}"' in mk, f"{var} must be QUOTED where it reaches docker run (word-split → bad image ref)"
    # CLAUDE_MOUNT is the deliberate exception: it carries multiple argv words.
    assert '"$$CLAUDE_MOUNT"' not in mk, "CLAUDE_MOUNT must stay unquoted — it is multiple argv words by design"


@pytest.mark.skipif(not (ROOT / "deploy" / "compose" / ".env.example").exists(), reason="no env example")
def test_admin_token_from_env_example_yields_one_argv_word():
    """End-to-end repro of the shipped bug, in the shell make actually uses.

    Before the fix this produced argv `[... , '#', 'admin-api', ...]` and `docker: invalid
    reference format`.
    """
    script = r"""
        envv() { grep -E "^$1=" "$2" | head -1 | cut -d= -f2- | sed -E 's/[[:space:]]+#.*$//; s/[[:space:]]+$//'; }
        ADMIN_TK=$(envv ADMIN_TOKEN "$3")
        n=0; for w in -e ADMIN_API_TOKEN="$ADMIN_TK" IMAGE; do n=$((n+1)); done
        echo $n
    """
    out = subprocess.run(
        ["/bin/sh", "-c", script, "sh", "ADMIN_TOKEN", str(ENV_EXAMPLE), str(ENV_EXAMPLE)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert out == "3", f"expected argv [-e, ADMIN_API_TOKEN=…, IMAGE] = 3 words, got {out}"
