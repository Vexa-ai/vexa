"""The drift gate: schema.sql must be exactly what the models generate — models are the SSOT
(house-style declarative SQLAlchemy), the file is the stdlib-consumable artifact."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_schema_sql_matches_models(tmp_path):
    import pytest
    try:
        import sqlalchemy  # noqa: F401
    except ImportError:
        pytest.skip("sqlalchemy not present in this env — generator runs where it is")
    before = (ROOT / "schema.sql").read_text()
    subprocess.run([sys.executable, str(ROOT / "scripts" / "gen_schema.py")],
                   check=True, capture_output=True)
    after = (ROOT / "schema.sql").read_text()
    assert before == after, "schema.sql drifted from schema_models.py — run scripts/gen_schema.py"
