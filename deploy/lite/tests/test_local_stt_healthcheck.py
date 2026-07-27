from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
LITE_DIR = ROOT / "deploy" / "lite"


# ALLOY: keep the downstream healthcheck adapter exact-opt-in at the generated command boundary.
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
