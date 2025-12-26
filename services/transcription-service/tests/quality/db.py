from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS test_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_name TEXT NOT NULL,
  created_at TEXT NOT NULL,
  api_url TEXT NOT NULL,
  meta_json TEXT NOT NULL,
  total_cases INTEGER NOT NULL DEFAULT 0,
  passed_cases INTEGER NOT NULL DEFAULT 0,
  failed_cases INTEGER NOT NULL DEFAULT 0,
  avg_wer REAL,
  avg_cer REAL
);

CREATE TABLE IF NOT EXISTS test_cases (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER NOT NULL,
  case_id TEXT NOT NULL,
  language TEXT,
  kind TEXT NOT NULL,
  snr_db REAL,
  audio_path TEXT NOT NULL,
  expected_text TEXT NOT NULL,
  transcript TEXT NOT NULL,
  wer REAL,
  cer REAL,
  passed INTEGER NOT NULL,
  error TEXT,
  duration_sec REAL,
  meta_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(run_id) REFERENCES test_runs(id)
);

CREATE INDEX IF NOT EXISTS idx_test_cases_run_id ON test_cases(run_id);
CREATE INDEX IF NOT EXISTS idx_test_cases_language ON test_cases(language);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA)
        conn.commit()


def start_run(db_path: Path, run_name: str, api_url: str, meta: dict[str, Any]) -> int:
    init_db(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO test_runs(run_name, created_at, api_url, meta_json) VALUES(?,?,?,?)",
            (run_name, utc_now(), api_url, json.dumps(meta, ensure_ascii=False)),
        )
        conn.commit()
        return int(cur.lastrowid)


def insert_case(
    db_path: Path,
    run_id: int,
    *,
    case_id: str,
    language: Optional[str],
    kind: str,
    snr_db: Optional[float],
    audio_path: str,
    expected_text: str,
    transcript: str,
    wer: Optional[float],
    cer: Optional[float],
    passed: bool,
    error: Optional[str],
    duration_sec: Optional[float],
    meta: dict[str, Any],
) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO test_cases(
              run_id, case_id, language, kind, snr_db, audio_path, expected_text,
              transcript, wer, cer, passed, error, duration_sec, meta_json, created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                run_id,
                case_id,
                language,
                kind,
                snr_db,
                audio_path,
                expected_text,
                transcript,
                wer,
                cer,
                1 if passed else 0,
                error or "",
                duration_sec,
                json.dumps(meta, ensure_ascii=False),
                utc_now(),
            ),
        )
        conn.commit()


def finalize_run(db_path: Path, run_id: int) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*), SUM(passed) FROM test_cases WHERE run_id=?", (run_id,))
        total, passed = cur.fetchone()
        total = int(total or 0)
        passed = int(passed or 0)
        failed = total - passed

        cur.execute(
            "SELECT AVG(wer), AVG(cer) FROM test_cases WHERE run_id=? AND wer IS NOT NULL",
            (run_id,),
        )
        avg_wer, avg_cer = cur.fetchone()

        cur.execute(
            """
            UPDATE test_runs
            SET total_cases=?, passed_cases=?, failed_cases=?, avg_wer=?, avg_cer=?
            WHERE id=?
            """,
            (total, passed, failed, avg_wer, avg_cer, run_id),
        )
        conn.commit()











