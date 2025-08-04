import requests
import json
from mcp.server.fastmcp import FastMCP
from typing import Dict, Any, List, Optional
import os

mcp = FastMCP("Vexa-MCP")


BASE_URL = "https://gateway.dev.vexa.ai"
VEXA_API_KEY = os.environ.get("VEXA_API_KEY")

HEADERS = {
    "X-API-Key": VEXA_API_KEY,
    "Content-Type": "application/json"
}


@mcp.tool()
def request_meeting_bot(meeting_id: str, language: Optional[str] = None, bot_name: Optional[str] = None, meeting_platform: str = "google_meet") -> Dict[str, Any]:
    """
    Request a Vexa bot to join a meeting for transcription.
    
    Args:
        meeting_id: The unique identifier for the meeting (e.g., 'xxx-xxxx-xxx' from Google Meet URL)
        language: Optional language code for transcription (e.g., 'en', 'es'). If not specified, auto-detected
        bot_name: Optional custom name for the bot in the meeting
        meeting_platform: The meeting platform (e.g., 'google_meet', 'zoom'). Default is 'google_meet'.
    
    Returns:
        JSON string with bot request details and status
    
    Note: After a successful request, it typically takes about 10 seconds for the bot to join the meeting.
    """

    request_bot_url = f"{BASE_URL}/bots"
    request_bot_payload = {
        "platform": meeting_platform,
        "native_meeting_id": meeting_id,
        "language": language, # Optional: specify language
        "bot_name": bot_name # Optional: custom name
    }

    try:
        response = requests.post(request_bot_url, headers=HEADERS, json=request_bot_payload, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.HTTPError as http_err:
        return {"error": "HTTP error occurred", "details": str(http_err), "status_code": getattr(http_err.response, 'status_code', None)}
    except requests.exceptions.Timeout:
        return {"error": "Request timed out"}
    except requests.exceptions.RequestException as req_err:
        return {"error": "Request failed", "details": str(req_err)}
    except Exception as e:
        return {"error": "An unexpected error occurred", "details": str(e)}

@mcp.tool()
def get_meeting_transcript(meeting_id: str, meeting_platform: str = "google_meet") -> Dict[str, Any]:
    """
    Get the real-time transcript for a meeting.
    
    Args:
        meeting_id: The unique identifier for the meeting
        meeting_platform: The meeting platform (e.g., 'google_meet', 'zoom'). Default is 'google_meet'.
    
    Returns:
        JSON with the meeting transcript data including segments with speaker, timestamp, and text
    
    Note: This provides real-time transcription data and can be called during or after the meeting.
    """
    get_transcript_url = f"{BASE_URL}/transcripts/{meeting_platform}/{meeting_id}"
    
    try:
        response = requests.get(get_transcript_url, headers=HEADERS, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.HTTPError as http_err:
        return {"error": "HTTP error occurred", "details": str(http_err), "status_code": getattr(http_err.response, 'status_code', None)}
    except requests.exceptions.Timeout:
        return {"error": "Request timed out"}
    except requests.exceptions.RequestException as req_err:
        return {"error": "Request failed", "details": str(req_err)}
    except Exception as e:
        return {"error": "An unexpected error occurred", "details": str(e)}


@mcp.tool()
def get_bot_status() -> Dict[str, Any]:
    """
    Get the status of currently running bots.
    
    Returns:
        JSON with details about active bots under your API key
    """
    get_status_url = f"{BASE_URL}/bots/status"
    
    try:
        response = requests.get(get_status_url, headers=HEADERS, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.HTTPError as http_err:
        return {"error": "HTTP error occurred", "details": str(http_err), "status_code": getattr(http_err.response, 'status_code', None)}
    except requests.exceptions.Timeout:
        return {"error": "Request timed out"}
    except requests.exceptions.RequestException as req_err:
        return {"error": "Request failed", "details": str(req_err)}
    except Exception as e:
        return {"error": "An unexpected error occurred", "details": str(e)}


@mcp.tool()
def update_bot_config(meeting_id: str, language: str, meeting_platform: str = "google_meet") -> Dict[str, Any]:
    """
    Update the configuration of an active bot (e.g., changing the language).
    
    Args:
        meeting_id: The identifier of the meeting with the active bot
        language: New language code for transcription (e.g., 'en', 'es')
        meeting_platform: The meeting platform (e.g., 'google_meet', 'zoom'). Default is 'google_meet'.
    
    Returns:
        JSON indicating whether the update request was accepted
    """
    update_config_url = f"{BASE_URL}/bots/{meeting_platform}/{meeting_id}/config"
    update_payload = {
        "language": language
    }
    
    try:
        response = requests.put(update_config_url, headers=HEADERS, json=update_payload, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.HTTPError as http_err:
        return {"error": "HTTP error occurred", "details": str(http_err), "status_code": getattr(http_err.response, 'status_code', None)}
    except requests.exceptions.Timeout:
        return {"error": "Request timed out"}
    except requests.exceptions.RequestException as req_err:
        return {"error": "Request failed", "details": str(req_err)}
    except Exception as e:
        return {"error": "An unexpected error occurred", "details": str(e)}


@mcp.tool()
def stop_bot(meeting_id: str, meeting_platform: str = "google_meet") -> Dict[str, Any]:
    """
    Remove an active bot from a meeting.
    
    Args:
        meeting_id: The identifier of the meeting
        meeting_platform: The meeting platform (e.g., 'google_meet', 'zoom'). Default is 'google_meet'.
    
    Returns:
        JSON confirming the bot removal
    """
    stop_bot_url = f"{BASE_URL}/bots/{meeting_platform}/{meeting_id}"
    
    try:
        response = requests.delete(stop_bot_url, headers=HEADERS, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.HTTPError as http_err:
        return {"error": "HTTP error occurred", "details": str(http_err), "status_code": getattr(http_err.response, 'status_code', None)}
    except requests.exceptions.Timeout:
        return {"error": "Request timed out"}
    except requests.exceptions.RequestException as req_err:
        return {"error": "Request failed", "details": str(req_err)}
    except Exception as e:
        return {"error": "An unexpected error occurred", "details": str(e)}


@mcp.tool()
def list_meetings() -> Dict[str, Any]:
    """
    List all meetings associated with your API key.
    
    Returns:
        JSON with a list of meeting records
    """
    list_meetings_url = f"{BASE_URL}/meetings"
    
    try:
        response = requests.get(list_meetings_url, headers=HEADERS, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.HTTPError as http_err:
        return {"error": "HTTP error occurred", "details": str(http_err), "status_code": getattr(http_err.response, 'status_code', None)}
    except requests.exceptions.Timeout:
        return {"error": "Request timed out"}
    except requests.exceptions.RequestException as req_err:
        return {"error": "Request failed", "details": str(req_err)}
    except Exception as e:
        return {"error": "An unexpected error occurred", "details": str(e)}


@mcp.tool()
def update_meeting_data(meeting_id: str, name: Optional[str] = None, participants: Optional[List[str]] = None, languages: Optional[List[str]] = None, notes: Optional[str] = None, meeting_platform: str = "google_meet") -> Dict[str, Any]:
    """
    Update meeting metadata such as name, participants, languages, and notes.
    
    Args:
        meeting_id: The unique identifier of the meeting
        name: Optional meeting name/title
        participants: Optional list of participant names
        languages: Optional list of language codes detected/used in the meeting
        notes: Optional meeting notes or description
        meeting_platform: The meeting platform (e.g., 'google_meet', 'zoom'). Default is 'google_meet'.
    
    Returns:
        JSON with the updated meeting record
    """
    update_meeting_url = f"{BASE_URL}/meetings/{meeting_platform}/{meeting_id}"
    
    # Build data payload with only provided fields
    data = {}
    if name is not None:
        data["name"] = name
    if participants is not None:
        data["participants"] = participants
    if languages is not None:
        data["languages"] = languages
    if notes is not None:
        data["notes"] = notes
    
    update_payload = {"data": data}
    
    try:
        response = requests.patch(update_meeting_url, headers=HEADERS, json=update_payload, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.HTTPError as http_err:
        return {"error": "HTTP error occurred", "details": str(http_err), "status_code": getattr(http_err.response, 'status_code', None)}
    except requests.exceptions.Timeout:
        return {"error": "Request timed out"}
    except requests.exceptions.RequestException as req_err:
        return {"error": "Request failed", "details": str(req_err)}
    except Exception as e:
        return {"error": "An unexpected error occurred", "details": str(e)}


@mcp.tool()
def delete_meeting(meeting_id: str, meeting_platform: str = "google_meet") -> Dict[str, Any]:
    """
    Permanently delete a meeting and all its associated transcripts.
    
    Args:
        meeting_id: The unique identifier of the meeting
        meeting_platform: The meeting platform (e.g., 'google_meet', 'zoom'). Default is 'google_meet'.
    
    Returns:
        JSON with confirmation message
    
    Warning: This action cannot be undone.
    """
    delete_meeting_url = f"{BASE_URL}/meetings/{meeting_platform}/{meeting_id}"
    
    try:
        response = requests.delete(delete_meeting_url, headers=HEADERS, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.HTTPError as http_err:
        return {"error": "HTTP error occurred", "details": str(http_err), "status_code": getattr(http_err.response, 'status_code', None)}
    except requests.exceptions.Timeout:
        return {"error": "Request timed out"}
    except requests.exceptions.RequestException as req_err:
        return {"error": "Request failed", "details": str(req_err)}
    except Exception as e:
        return {"error": "An unexpected error occurred", "details": str(e)}


if __name__ == "__main__":
    mcp.run(transport="stdio")