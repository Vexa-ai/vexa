import logging
from typing import List, Dict, Any, Optional, Tuple
import json

logger = logging.getLogger(__name__)

# Speaker mapping statuses
STATUS_UNKNOWN = "UNKNOWN"
STATUS_MAPPED = "MAPPED"
STATUS_MULTIPLE = "MULTIPLE" # Not fully implemented in terms of selection logic here, defaults to last one for now
STATUS_NO_SPEAKER_EVENTS = "NO_SPEAKER_EVENTS"
STATUS_ERROR = "ERROR"

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
    
    candidate_speakers = {} # participant_id_meet -> last_start_event

    for event in parsed_events:
        event_ts = event['relative_client_timestamp_ms']
        participant_id = event.get("participant_id_meet") or event.get("participant_name") # Fallback to name if id_meet missing

        if not participant_id:
            continue

        if event["event_type"] == "SPEAKER_START":
            # If this start is before the segment ends, it *could* be the speaker
            if event_ts <= segment_end_ms:
                candidate_speakers[participant_id] = event
            # If this start is after segment ends, it and subsequent events for this speaker are irrelevant
            # (assuming chronological sort of input `parsed_events`)
            # else: break # Optimization: if events are globally sorted by time

        elif event["event_type"] == "SPEAKER_END":
            # If this end event is for a candidate and occurs *before* the segment starts,
            # then that candidate is no longer speaking.
            if participant_id in candidate_speakers and event_ts < segment_start_ms:
                del candidate_speakers[participant_id]
    
    # From the remaining candidates, determine who was speaking during the segment
    # This logic can be complex for overlaps. Simplified: take the one whose START was latest but before/at segment start.
    # More robust: find speaker whose active interval [speaker_start, speaker_end_or_session_end] maximally overlaps segment.
    
    best_candidate_name: Optional[str] = None
    best_candidate_id: Optional[str] = None
    latest_start_time_before_segment_end = -1

    active_speakers_in_segment = []

    for p_id, start_event in candidate_speakers.items():
        start_ts = start_event['relative_client_timestamp_ms']
        # Find corresponding END event for this p_id that is after start_ts
        end_ts = session_end_time_ms or segment_end_ms # Default to session_end or segment_end if no specific end event
        # look for an explicit end event
        for end_search_event in parsed_events: # Search all parsed events again for the corresponding end
            if (end_search_event.get("participant_id_meet") == p_id or end_search_event.get("participant_name") == p_id) and \
               end_search_event["event_type"] == "SPEAKER_END" and \
               end_search_event['relative_client_timestamp_ms'] >= start_ts:
                end_ts = end_search_event['relative_client_timestamp_ms']
                break # Found the earliest relevant END event
        
        # Speaker is active during the segment if: [start_ts, end_ts] overlaps with [segment_start_ms, segment_end_ms]
        # Overlap condition: max(start1, start2) < min(end1, end2)
        overlap_start = max(start_ts, segment_start_ms)
        overlap_end = min(end_ts, segment_end_ms)

        if overlap_start < overlap_end: # If there is an overlap
            active_speakers_in_segment.append({
                "name": start_event["participant_name"],
                "id": start_event.get("participant_id_meet"),
                "overlap_duration": overlap_end - overlap_start,
                "start_event_ts": start_ts
            })

    if not active_speakers_in_segment:
        mapping_status = STATUS_UNKNOWN
    elif len(active_speakers_in_segment) == 1:
        active_speaker_name = active_speakers_in_segment[0]["name"]
        active_participant_id = active_speakers_in_segment[0]["id"]
        mapping_status = STATUS_MAPPED
    else:
        # Multiple speakers overlap. Prioritize by longest overlap.
        # If overlaps are equal, could use other heuristics (e.g. latest start). For now, longest.
        active_speakers_in_segment.sort(key=lambda x: x["overlap_duration"], reverse=True)
        active_speaker_name = active_speakers_in_segment[0]["name"]
        active_participant_id = active_speakers_in_segment[0]["id"]
        mapping_status = STATUS_MULTIPLE
        logger.info(f"Multiple speakers found for segment {segment_start_ms}-{segment_end_ms}. Selected {active_speaker_name} due to longest overlap.")

    return {
        "speaker_name": active_speaker_name,
        "participant_id_meet": active_participant_id,
        "status": mapping_status
    } 