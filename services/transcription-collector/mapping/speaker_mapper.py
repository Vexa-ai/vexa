import logging
from typing import List, Dict, Any, Optional, Tuple
import json
import redis.asyncio as aioredis
import redis

logger = logging.getLogger(__name__)

# Speaker mapping statuses
STATUS_UNKNOWN = "UNKNOWN"
STATUS_MAPPED = "MAPPED"
STATUS_MULTIPLE = "MULTIPLE_CONCURRENT_SPEAKERS"
STATUS_NO_SPEAKER_EVENTS = "NO_SPEAKER_EVENTS"
STATUS_ERROR = "ERROR_IN_MAPPING"

# NEW: Define buffer constants for fetching speaker events
# CRITICAL: We need to fetch events from session start (0) to catch all active speakers,
# not just a small buffer, because a speaker who started earlier might still be active
PRE_SEGMENT_SPEAKER_EVENT_FETCH_MS = 0  # Fetch from session start (0ms) to catch all active speakers
POST_SEGMENT_SPEAKER_EVENT_FETCH_MS = 500 # Small buffer after segment end for late-arriving END events

def _get_participant_identifier(event: Dict[str, Any]) -> Optional[str]:
    """Extract a consistent participant identifier from an event.
    Returns participant_id_meet if available, otherwise participant_name.
    This ensures consistent matching between START and END events.
    """
    return event.get("participant_id_meet") or event.get("participant_name")

def _events_match_participant(event1: Dict[str, Any], event2: Dict[str, Any]) -> bool:
    """Check if two events belong to the same participant.
    Matches on either participant_id_meet or participant_name.
    """
    id1 = _get_participant_identifier(event1)
    id2 = _get_participant_identifier(event2)
    if not id1 or not id2:
        return False
    
    # Direct match
    if id1 == id2:
        return True
    
    # Cross-match: check if id1 matches id2's other field
    if event1.get("participant_id_meet") == event2.get("participant_id_meet") and event1.get("participant_id_meet"):
        return True
    if event1.get("participant_name") == event2.get("participant_name") and event1.get("participant_name"):
        return True
    
    return False

def map_speaker_to_segment(
    segment_start_ms: float,
    segment_end_ms: float,
    speaker_events_for_session: List[Tuple[str, float]], # List of (event_json_str, timestamp_ms)
    session_end_time_ms: Optional[float] = None
) -> Dict[str, Any]:
    """Maps a speaker to a transcription segment based on speaker events.

    Args:
        segment_start_ms: Start time of the transcription segment in milliseconds.
        segment_end_ms: End time of the transcription segment in milliseconds.
        speaker_events_for_session: Chronologically sorted list of speaker event (JSON string, timestamp_ms) tuples.
        session_end_time_ms: The official end time of the session in milliseconds, if available.
                           Used for handling open SPEAKER_START events at the end of a session.

    Returns:
        A dictionary containing:
            'speaker_name': Name of the identified speaker, or None.
            'participant_id_meet': Google Meet participant ID, or None.
            'status': Mapping status (e.g., MAPPED, UNKNOWN, MULTIPLE).
    """
    active_speaker_name: Optional[str] = None
    active_participant_id: Optional[str] = None
    mapping_status = STATUS_UNKNOWN

    if not speaker_events_for_session:
        return {
            "speaker_name": None, 
            "participant_id_meet": None, 
            "status": STATUS_NO_SPEAKER_EVENTS
        }

    # Parse speaker events from JSON string to dict
    parsed_events: List[Dict[str, Any]] = []
    for event_json, timestamp in speaker_events_for_session:
        try:
            event = json.loads(event_json)
            event['relative_client_timestamp_ms'] = timestamp # Ensure timestamp is part of the event dict
            parsed_events.append(event)
        except json.JSONDecodeError:
            logger.warning(f"Failed to parse speaker event JSON: {event_json}")
            continue
    
    if not parsed_events:
        return {"speaker_name": None, "participant_id_meet": None, "status": STATUS_ERROR} # Error parsing all events

    # Find speaker(s) active during the segment interval
    # This is a simplified approach: considers the speaker whose START event is closest before or at segment_start_ms
    # and whose corresponding END event is after segment_start_ms or not present before segment_end_ms.

    # Relevant events are those whose activity period could overlap with the segment
    # A speaker is active in segment [S_start, S_end] if:
    #   - They have a START event at T_start <= S_end
    #   - And no corresponding END event T_end such that T_start <= T_end < S_start
    
    # Use a list to store candidates with their events, since we need to match by both ID and name
    candidate_speakers: List[Dict[str, Any]] = []  # List of {event, identifier}

    logger.debug(f"[MapSpeaker] Segment: [{segment_start_ms:.0f}ms, {segment_end_ms:.0f}ms], Processing {len(parsed_events)} events")

    for event in parsed_events:
        event_ts = event['relative_client_timestamp_ms']
        participant_id = _get_participant_identifier(event)
        participant_name = event.get("participant_name", "unknown")

        if not participant_id:
            logger.warning(f"[MapSpeaker] Event at {event_ts:.0f}ms missing both participant_id_meet and participant_name: {event}")
            continue

        if event["event_type"] == "SPEAKER_START":
            # If this start is before the segment ends, it *could* be the speaker
            if event_ts <= segment_end_ms:
                # Check if we already have a START for this participant (replace if newer)
                existing_idx = None
                for idx, candidate in enumerate(candidate_speakers):
                    if _events_match_participant(candidate["event"], event):
                        existing_idx = idx
                        break
                
                if existing_idx is not None:
                    # Replace with newer START event
                    candidate_speakers[existing_idx] = {"event": event, "identifier": participant_id}
                    logger.debug(f"[MapSpeaker] Updated candidate: {participant_name} (ID: {participant_id}) at {event_ts:.0f}ms")
                else:
                    candidate_speakers.append({"event": event, "identifier": participant_id})
                    logger.debug(f"[MapSpeaker] Added candidate: {participant_name} (ID: {participant_id}) at {event_ts:.0f}ms")
            else:
                logger.debug(f"[MapSpeaker] Skipping SPEAKER_START for {participant_name} at {event_ts:.0f}ms (after segment end {segment_end_ms:.0f}ms)")

        elif event["event_type"] == "SPEAKER_END":
            # Find matching candidate and remove if END occurs before segment starts
            matching_idx = None
            for idx, candidate in enumerate(candidate_speakers):
                if _events_match_participant(candidate["event"], event):
                    matching_idx = idx
                    break
            
            if matching_idx is not None:
                if event_ts < segment_start_ms:
                    removed_name = candidate_speakers[matching_idx]["event"].get("participant_name", "unknown")
                    logger.debug(f"[MapSpeaker] Removing candidate {removed_name} (ID: {participant_id}) - END at {event_ts:.0f}ms before segment start {segment_start_ms:.0f}ms")
                    candidate_speakers.pop(matching_idx)
                else:
                    logger.debug(f"[MapSpeaker] Keeping candidate {participant_name} (ID: {participant_id}) - END at {event_ts:.0f}ms during/after segment")
            else:
                logger.debug(f"[MapSpeaker] SPEAKER_END for {participant_name} (ID: {participant_id}) at {event_ts:.0f}ms - not in candidates")
    
    logger.debug(f"[MapSpeaker] After filtering: {len(candidate_speakers)} candidate(s): {[c['event'].get('participant_name') for c in candidate_speakers]}")
    
    # From the remaining candidates, determine who was speaking during the segment
    # This logic can be complex for overlaps. Simplified: take the one whose START was latest but before/at segment start.
    # More robust: find speaker whose active interval [speaker_start, speaker_end_or_session_end] maximally overlaps segment.

    active_speakers_in_segment = []

    for candidate in candidate_speakers:
        start_event = candidate["event"]
        start_ts = start_event['relative_client_timestamp_ms']
        participant_name = start_event.get("participant_name", "unknown")
        participant_id = _get_participant_identifier(start_event)
        
        # Find corresponding END event for this participant that is after start_ts
        end_ts = session_end_time_ms or segment_end_ms # Default to session_end or segment_end if no specific end event
        found_end_event = False
        # look for an explicit end event
        for end_search_event in parsed_events: # Search all parsed events again for the corresponding end
            if _events_match_participant(start_event, end_search_event) and \
               end_search_event["event_type"] == "SPEAKER_END" and \
               end_search_event['relative_client_timestamp_ms'] >= start_ts:
                end_ts = end_search_event['relative_client_timestamp_ms']
                found_end_event = True
                logger.debug(f"[MapSpeaker] Found END event for {participant_name} (ID: {participant_id}) at {end_ts:.0f}ms")
                break # Found the earliest relevant END event
        
        if not found_end_event:
            logger.debug(f"[MapSpeaker] No END event found for {participant_name} (ID: {participant_id}), using default end_ts={end_ts:.0f}ms")
        
        # Speaker is active during the segment if: [start_ts, end_ts] overlaps with [segment_start_ms, segment_end_ms]
        # Overlap condition: max(start1, start2) < min(end1, end2)
        overlap_start = max(start_ts, segment_start_ms)
        overlap_end = min(end_ts, segment_end_ms)

        logger.debug(f"[MapSpeaker] {participant_name}: active [{start_ts:.0f}ms, {end_ts:.0f}ms], segment [{segment_start_ms:.0f}ms, {segment_end_ms:.0f}ms], overlap [{overlap_start:.0f}ms, {overlap_end:.0f}ms]")

        if overlap_start < overlap_end: # If there is an overlap
            overlap_duration = overlap_end - overlap_start
            logger.debug(f"[MapSpeaker] {participant_name} overlaps with segment: {overlap_duration:.0f}ms")
            active_speakers_in_segment.append({
                "name": start_event["participant_name"],
                "id": start_event.get("participant_id_meet"),
                "overlap_duration": overlap_duration,
                "start_event_ts": start_ts
            })
        else:
            logger.debug(f"[MapSpeaker] {participant_name} does NOT overlap with segment (overlap_start >= overlap_end)")

    if not active_speakers_in_segment:
        logger.warning(f"[MapSpeaker] No active speakers found for segment [{segment_start_ms:.0f}ms, {segment_end_ms:.0f}ms] - returning UNKNOWN")
        mapping_status = STATUS_UNKNOWN
    elif len(active_speakers_in_segment) == 1:
        active_speaker_name = active_speakers_in_segment[0]["name"]
        active_participant_id = active_speakers_in_segment[0]["id"]
        mapping_status = STATUS_MAPPED
        logger.info(f"[MapSpeaker] MAPPED segment [{segment_start_ms:.0f}ms, {segment_end_ms:.0f}ms] to {active_speaker_name} (ID: {active_participant_id})")
    else:
        # Multiple speakers overlap. Prioritize by longest overlap.
        # If overlaps are equal, could use other heuristics (e.g. latest start). For now, longest.
        active_speakers_in_segment.sort(key=lambda x: x["overlap_duration"], reverse=True)
        active_speaker_name = active_speakers_in_segment[0]["name"]
        active_participant_id = active_speakers_in_segment[0]["id"]
        mapping_status = STATUS_MULTIPLE
        logger.info(f"[MapSpeaker] MULTIPLE speakers for segment [{segment_start_ms:.0f}ms, {segment_end_ms:.0f}ms]. Selected {active_speaker_name} (overlap: {active_speakers_in_segment[0]['overlap_duration']:.0f}ms) over {len(active_speakers_in_segment)-1} other(s)")

    return {
        "speaker_name": active_speaker_name,
        "participant_id_meet": active_participant_id,
        "status": mapping_status
    } 

# NEW Utility function to centralize fetching and mapping logic
async def get_speaker_mapping_for_segment(
    redis_c: 'aioredis.Redis', # Forward reference for type hint
    session_uid: str,
    segment_start_ms: float,
    segment_end_ms: float,
    config_speaker_event_key_prefix: str, # Pass REDIS_SPEAKER_EVENT_KEY_PREFIX
    context_log_msg: str = "" # For more specific logging, e.g., "[LiveMap]" or "[FinalMap]"
) -> Dict[str, Any]:
    """
    Fetches speaker events from Redis for a given segment and session, 
    then maps them to determine the speaker.
    """
    if not session_uid:
        logger.warning(f"{context_log_msg} No session_uid provided. Cannot map speakers.")
        return {"speaker_name": None, "participant_id_meet": None, "status": STATUS_UNKNOWN}

    mapped_speaker_name: Optional[str] = None
    mapping_status: str = STATUS_UNKNOWN
    active_participant_id: Optional[str] = None

    try:
        speaker_event_key = f"{config_speaker_event_key_prefix}:{session_uid}"
        
        # CRITICAL FIX: Fetch ALL events from session start (0) to segment_end + buffer
        # This ensures we catch speakers who started speaking earlier and are still active
        # Previous bug: Only fetched events in small 500ms window, missing earlier START events
        fetch_start_ms = 0  # Always fetch from session start to catch all active speakers
        fetch_end_ms = segment_end_ms + POST_SEGMENT_SPEAKER_EVENT_FETCH_MS
        
        logger.debug(f"{context_log_msg} Fetching speaker events from Redis: [{fetch_start_ms:.0f}ms, {fetch_end_ms:.0f}ms]")
        
        # Fetch speaker events from Redis
        speaker_events_raw = await redis_c.zrangebyscore(
            speaker_event_key, 
            min=fetch_start_ms,
            max=fetch_end_ms,
            withscores=True
        )

        # --- Debug-mode instrumentation (docker logs; no PII) ---
        # #region agent log
        try:
            # Check if there are late speaker events beyond our buffer window.
            # This helps validate whether POST_SEGMENT_SPEAKER_EVENT_FETCH_MS is too small.
            late_min = fetch_end_ms
            late_max = segment_end_ms + 3000  # 3s after segment end
            if late_max > late_min:
                late_events_raw = await redis_c.zrangebyscore(
                    speaker_event_key,
                    min=late_min,
                    max=late_max,
                    withscores=False
                )
                if late_events_raw:
                    # Summarize by event_type and unique participant ids (hashed-ish by suffix/prefix)
                    counts: Dict[str, int] = {}
                    ids: set[str] = set()
                    for ev in late_events_raw[:50]:  # cap for safety
                        try:
                            ev_str = ev.decode("utf-8") if isinstance(ev, (bytes, bytearray)) else str(ev)
                            obj = json.loads(ev_str)
                            et = str(obj.get("event_type") or "unknown")
                            counts[et] = counts.get(et, 0) + 1
                            pid = obj.get("participant_id_meet") or obj.get("participant_name") or ""
                            pid = str(pid)
                            if pid:
                                ids.add(pid[:8])
                        except Exception:
                            counts["unparseable"] = counts.get("unparseable", 0) + 1
                    logger.info(
                        f"{context_log_msg} [DiagLateEvents] UID:{session_uid[:8]} "
                        f"segEnd={segment_end_ms:.0f}ms bufferEnd={fetch_end_ms:.0f}ms lateCount={len(late_events_raw)} "
                        f"types={counts} participantIdPrefixes={sorted(list(ids))[:5]}"
                    )
        except Exception as _diag_err:
            logger.debug(f"{context_log_msg} [DiagLateEvents] failed: {_diag_err}")
        # #endregion
        
        speaker_events_for_mapper: List[Tuple[str, float]] = []
        for event_data, score_ms in speaker_events_raw:
            event_json_str: Optional[str] = None
            if isinstance(event_data, bytes):
                event_json_str = event_data.decode('utf-8')
            elif isinstance(event_data, str):
                event_json_str = event_data
            else:
                logger.warning(f"{context_log_msg} UID:{session_uid} Seg:{segment_start_ms}-{segment_end_ms} Unexpected speaker event data type from Redis: {type(event_data)}. Skipping this event.")
                continue
            speaker_events_for_mapper.append((event_json_str, float(score_ms)))

        log_prefix_detail = f"{context_log_msg} UID:{session_uid} Seg:{segment_start_ms:.0f}-{segment_end_ms:.0f}ms"

        if not speaker_events_for_mapper:
            logger.debug(f"{log_prefix_detail} No speaker events in Redis for mapping.")
            mapping_status = STATUS_NO_SPEAKER_EVENTS
        else:
            logger.debug(f"{log_prefix_detail} {len(speaker_events_for_mapper)} speaker events for mapping.")

        # Call the core mapping logic
        mapping_result = map_speaker_to_segment(
            segment_start_ms=segment_start_ms,
            segment_end_ms=segment_end_ms,
            speaker_events_for_session=speaker_events_for_mapper, # Now contains all events for the session
            session_end_time_ms=None # session_end_time not critical for per-segment mapping here
        )
        
        mapped_speaker_name = mapping_result.get("speaker_name")
        active_participant_id = mapping_result.get("participant_id_meet")
        mapping_status = mapping_result.get("status", STATUS_ERROR)
        
        if mapping_status != STATUS_NO_SPEAKER_EVENTS: # Avoid double logging if no events
             logger.info(f"{log_prefix_detail} Result: Name='{mapped_speaker_name}', Status='{mapping_status}'")

    except redis.exceptions.RedisError as re:
        logger.error(f"{context_log_msg} UID:{session_uid} Seg:{segment_start_ms}-{segment_end_ms} Redis error fetching/processing speaker events: {re}", exc_info=True)
        mapping_status = STATUS_ERROR 
    except Exception as map_err:
        logger.error(f"{context_log_msg} UID:{session_uid} Seg:{segment_start_ms}-{segment_end_ms} Speaker mapping error: {map_err}", exc_info=True)
        mapping_status = STATUS_ERROR
    
    return {
        "speaker_name": mapped_speaker_name,
        "participant_id_meet": active_participant_id,
        "status": mapping_status
    } 