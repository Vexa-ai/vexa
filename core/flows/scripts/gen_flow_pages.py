#!/usr/bin/env python3
"""Write `behavior/global/flows/<flow>.md` FROM the flow registry — the page is derived, the code is
the contract; run after a step changes (make flow-pages). `tests/test_flow_pages.py` fails until
you have."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from flows_pages import PAGES_DIR, all_pages  # noqa: E402

out = Path(__file__).resolve().parents[3].joinpath(*PAGES_DIR)
out.mkdir(parents=True, exist_ok=True)
pages = all_pages()
for name, body in sorted(pages.items()):
    (out / name).write_text(body, encoding="utf-8")
stale = [f.name for f in out.iterdir() if f.is_file() and f.name not in pages]
for name in stale:
    (out / name).unlink()
print(f"wrote {len(pages)} page(s) to {out}"
      + (f" · removed {', '.join(sorted(stale))}" if stale else ""))
