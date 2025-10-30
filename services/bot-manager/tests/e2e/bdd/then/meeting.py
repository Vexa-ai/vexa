"""
THEN helpers for meeting state assertions.
"""


def meeting_should_be_marked_as_completed(meeting, original_status, original_end_time):
    """THEN meeting should be marked as completed with updated timestamp."""
    assert meeting.status == "completed", f"Meeting status should be 'completed', got '{meeting.status}'"
    assert meeting.end_time is not None, "Meeting end_time should be set"
    assert meeting.end_time != original_end_time, "Meeting end_time should have been updated"


def meeting_should_have_status(meeting, expected_status: str):
    """THEN meeting should have the expected status."""
    assert meeting.status == expected_status, f"Meeting status should be '{expected_status}', got '{meeting.status}'"