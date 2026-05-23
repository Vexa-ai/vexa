#!/usr/bin/env python3
"""Score a meeting-tts run against ground truth plus telemetry."""

from __future__ import annotations

import argparse
import json
import re
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            rows.append({"raw": line})
    return rows


def parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def seconds_between(start: datetime | None, end: datetime | None) -> float | None:
    if start is None or end is None:
        return None
    return round((end - start).total_seconds(), 3)


def normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def tokens(text: str) -> set[str]:
    stop = {
        "a",
        "an",
        "and",
        "are",
        "as",
        "be",
        "by",
        "can",
        "for",
        "from",
        "i",
        "in",
        "is",
        "it",
        "of",
        "or",
        "so",
        "that",
        "the",
        "this",
        "to",
        "we",
        "will",
        "with",
    }
    return {t for t in normalize(text).split() if len(t) > 2 and t not in stop}


def parse_ground_truth(path: Path, case_id: str, run_id: str) -> tuple[list[str], list[dict[str, str]]]:
    truth = path.read_text(encoding="utf-8").replace("CASE_ID", case_id).replace("RUN_ID", run_id)
    anchors_match = re.search(r"Key anchors:\s*(.*)", truth)
    anchors: list[str] = []
    if anchors_match:
        anchors = [a.strip(" `.") for a in anchors_match.group(1).split(",") if a.strip(" `.")]
    turns: list[dict[str, str]] = []
    for match in re.finditer(r"^\s*(\d+)\.\s+`([^`]+)`:\s+(.*)$", truth, re.M):
        meta = match.group(2).split("|")
        turns.append(
            {
                "index": match.group(1),
                "speaker_id": meta[0],
                "speaker_name": meta[1] if len(meta) > 1 else meta[0],
                "voice": meta[2] if len(meta) > 2 else "",
                "text": match.group(3),
            }
        )
    return anchors, turns


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return round(values[0], 3)
    values = sorted(values)
    index = (len(values) - 1) * pct
    lower = int(index)
    upper = min(lower + 1, len(values) - 1)
    weight = index - lower
    return round(values[lower] * (1 - weight) + values[upper] * weight, 3)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--ground-truth", type=Path, required=True)
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()

    transcript = load_json(args.run_dir / "transcript.json", {})
    segments = transcript.get("segments", transcript if isinstance(transcript, list) else [])
    if not isinstance(segments, list):
        segments = []
    speak_events = load_jsonl(args.run_dir / "speak-events.jsonl")
    listener_meeting = load_json(args.run_dir / "listener-meeting.json", {})
    human = {}
    human_path = args.run_dir / "human-confirmation.env"
    if human_path.exists():
        for line in human_path.read_text(encoding="utf-8").splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                human[key] = value
    playback = {}
    playback_path = args.run_dir / "playback-confirmation.env"
    if playback_path.exists():
        for line in playback_path.read_text(encoding="utf-8").splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                playback[key] = value

    anchors, turns = parse_ground_truth(args.ground_truth, args.case_id, args.run_id)
    transcript_text = " ".join(str(s.get("text") or "") for s in segments if isinstance(s, dict))
    normalized_transcript = normalize(transcript_text)
    matched_anchors = [a for a in anchors if normalize(a) in normalized_transcript]

    speak_times = [parse_dt(e.get("at")) for e in speak_events if isinstance(e, dict)]
    speak_times = [t for t in speak_times if t is not None]
    first_speak = min(speak_times) if speak_times else None
    last_speak = max(speak_times) if speak_times else None
    transcript_window_start = first_speak - timedelta(seconds=30) if first_speak else None
    transcript_window_end = last_speak + timedelta(seconds=180) if last_speak else None

    segment_rows: list[dict[str, Any]] = []
    segment_created_times: list[datetime] = []
    filtered_out_segments = 0
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        created_at = parse_dt(segment.get("created_at"))
        if (
            created_at
            and transcript_window_start
            and transcript_window_end
            and (created_at < transcript_window_start or created_at > transcript_window_end)
        ):
            filtered_out_segments += 1
            continue
        if created_at:
            segment_created_times.append(created_at)
        segment_rows.append(
            {
                "text": str(segment.get("text") or ""),
                "speaker": segment.get("speaker"),
                "created_at": segment.get("created_at"),
                "absolute_start_time": segment.get("absolute_start_time"),
                "absolute_end_time": segment.get("absolute_end_time"),
            }
        )

    turn_matches: list[dict[str, Any]] = []
    matched_turns = 0
    speaker_correct = 0
    speaker_wrong = 0
    speaker_unknown = 0
    for turn in turns:
        expected_tokens = tokens(turn["text"])
        best: dict[str, Any] | None = None
        best_score = 0.0
        best_expected: dict[str, Any] | None = None
        best_expected_score = 0.0
        best_wrong: dict[str, Any] | None = None
        best_wrong_score = 0.0
        for segment in segment_rows:
            found_tokens = tokens(segment["text"])
            if not expected_tokens:
                continue
            score = len(expected_tokens & found_tokens) / max(1, len(expected_tokens))
            if score > best_score:
                best_score = score
                best = segment
            speaker_text = str(segment.get("speaker") or "")
            if turn["speaker_name"].lower() in speaker_text.lower():
                if score > best_expected_score:
                    best_expected_score = score
                    best_expected = segment
            elif speaker_text and score > best_wrong_score:
                best_wrong_score = score
                best_wrong = segment
        matched = best_score >= 0.25 or best_expected_score >= 0.25
        if matched:
            matched_turns += 1
        speaker_state = "unknown"
        chosen = best
        chosen_score = best_score
        if matched and best_expected_score >= 0.25:
            speaker_state = "correct"
            speaker_correct += 1
            chosen = best_expected
            chosen_score = best_expected_score
        elif matched and best_wrong_score >= 0.25:
            speaker_state = "wrong_or_merged"
            speaker_wrong += 1
            chosen = best_wrong
            chosen_score = best_wrong_score
        else:
            if matched:
                speaker_unknown += 1
        turn_matches.append(
            {
                "turn": int(turn["index"]),
                "expected_speaker": turn["speaker_name"],
                "voice": turn["voice"],
                "matched": matched,
                "overlap_score": round(chosen_score, 3),
                "best_overall_score": round(best_score, 3),
                "best_expected_speaker_score": round(best_expected_score, 3),
                "best_wrong_speaker_score": round(best_wrong_score, 3),
                "matched_transcript_speaker": chosen.get("speaker") if chosen else None,
                "speaker_match": speaker_state,
                "matched_text_prefix": (chosen.get("text") or "")[:120] if chosen else "",
            }
        )

    first_transcript = min(segment_created_times) if segment_created_times else None
    last_transcript = max(segment_created_times) if segment_created_times else None
    per_speak_lags: list[float] = []
    for event_time in speak_times:
        next_created = min((t for t in segment_created_times if t >= event_time), default=None)
        lag = seconds_between(event_time, next_created)
        if lag is not None:
            per_speak_lags.append(lag)

    data = listener_meeting.get("data") if isinstance(listener_meeting, dict) else {}
    data = data if isinstance(data, dict) else {}
    status_deliveries = data.get("webhook_deliveries") or []
    completion = data.get("webhook_delivery") or {}
    recording = data.get("recording") or data.get("recording_metadata") or {}
    recordings = data.get("recordings") or []
    if isinstance(recordings, list) and recordings:
        recording = recordings[0]
    recording_status = None
    recording_id = None
    playback_url = None
    if isinstance(recording, dict):
        recording_status = data.get("recording_status") or recording.get("status")
        recording_id = data.get("recording_id") or recording.get("id") or recording.get("recording_id")
        playback_url = (
            data.get("playback_url")
            or data.get("recording_url")
            or data.get("master_url")
            or recording.get("playback_url")
            or recording.get("recording_url")
            or recording.get("master_url")
        )
    else:
        recording_status = data.get("recording_status")
        recording_id = data.get("recording_id")
        playback_url = data.get("playback_url") or data.get("recording_url") or data.get("master_url")
    if isinstance(playback_url, dict):
        playback_url = playback_url.get("audio") or playback_url.get("video")
    webhook_rows = []
    for delivery in status_deliveries if isinstance(status_deliveries, list) else []:
        if not isinstance(delivery, dict):
            continue
        webhook_rows.append(
            {
                "event_type": delivery.get("event_type"),
                "status": delivery.get("status"),
                "attempts": delivery.get("attempts"),
                "status_code": delivery.get("status_code") or delivery.get("http_status"),
                "delivered_at": delivery.get("delivered_at"),
                "next_retry_at": delivery.get("next_retry_at"),
                "last_error": delivery.get("last_error"),
            }
        )
    if isinstance(completion, dict) and completion:
        webhook_rows.append(
            {
                "event_type": "meeting.completed",
                "status": completion.get("status"),
                "attempts": completion.get("attempts") or completion.get("retry_count"),
                "status_code": completion.get("status_code") or completion.get("http_status"),
                "delivered_at": completion.get("delivered_at"),
                "last_error": completion.get("last_error") or completion.get("error"),
            }
        )

    output = {
        "ground_truth": {
            "expected_turns": len(turns),
            "matched_turns": matched_turns,
            "content_turn_match_rate": round(matched_turns / len(turns), 3) if turns else None,
            "key_anchors_total": len(anchors),
            "key_anchors_matched": len(matched_anchors),
            "matched_anchors": matched_anchors,
            "raw_transcript_segments": len(segments),
            "scored_transcript_segments": len(segment_rows),
            "filtered_out_segments": filtered_out_segments,
        },
        "speaker_identification": {
            "labels_seen": sorted({str(s.get("speaker")) for s in segments if isinstance(s, dict) and s.get("speaker")}),
            "correct_matched_turns": speaker_correct,
            "wrong_or_merged_matched_turns": speaker_wrong,
            "unknown_turns": speaker_unknown,
        },
        "latency": {
            "first_speak_at": first_speak.isoformat().replace("+00:00", "Z") if first_speak else None,
            "first_transcript_created_at": first_transcript.isoformat().replace("+00:00", "Z") if first_transcript else None,
            "first_transcript_latency_seconds": seconds_between(first_speak, first_transcript),
            "last_speak_at": last_speak.isoformat().replace("+00:00", "Z") if last_speak else None,
            "last_transcript_created_at": last_transcript.isoformat().replace("+00:00", "Z") if last_transcript else None,
            "last_transcript_lag_seconds": seconds_between(last_speak, last_transcript),
            "transcript_window_start": transcript_window_start.isoformat().replace("+00:00", "Z")
            if transcript_window_start
            else None,
            "transcript_window_end": transcript_window_end.isoformat().replace("+00:00", "Z")
            if transcript_window_end
            else None,
            "per_speak_to_next_transcript_count": len(per_speak_lags),
            "per_speak_to_next_transcript_min_seconds": min(per_speak_lags) if per_speak_lags else None,
            "per_speak_to_next_transcript_median_seconds": round(statistics.median(per_speak_lags), 3) if per_speak_lags else None,
            "per_speak_to_next_transcript_p95_seconds": percentile(per_speak_lags, 0.95),
            "per_speak_to_next_transcript_max_seconds": max(per_speak_lags) if per_speak_lags else None,
        },
        "webhooks": {
            "target_configured": bool(data.get("webhook_url")),
            "deliveries": webhook_rows,
            "delivered_count": sum(1 for row in webhook_rows if row.get("status") == "delivered"),
            "retrying_count": sum(1 for row in webhook_rows if row.get("status") == "retrying"),
            "failed_count": sum(1 for row in webhook_rows if row.get("status") == "failed"),
        },
        "recording": {
            "recording_enabled": data.get("recording_enabled"),
            "recording_count": len(recordings) if isinstance(recordings, list) else None,
            "recording_status": recording_status,
            "recording_id_present": bool(recording_id),
            "playback_or_master_url_present": bool(playback_url),
        },
        "human_confirmation": human,
        "playback_confirmation": playback,
        "turn_matches": turn_matches,
    }
    (args.run_dir / "telemetry-score.json").write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(json.dumps(output, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
