import json
import os
from fastapi import FastAPI, Header, HTTPException, Depends
from fastapi_mcp import FastApiMCP
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field
import httpx

app = FastAPI()

BASE_URL = os.getenv("API_GATEWAY_URL", "http://api-gateway:8000")

# ---------------------------
# Dependencies & Utilities
# ---------------------------
async def get_api_key(authorization: Optional[str] = Header(None)) -> str:
    """
    Extract API key from Authorization header.
    Expected format: "YOUR_API_KEY"
    """
    return authorization


def get_headers(api_key: str) -> Dict[str, str]:
    """Create headers with the provided API key"""
    return {
        "X-API-Key": api_key,
        "Content-Type": "application/json"
    }


# ---------------------------
# Request Models
# ---------------------------
class RequestMeetingBot(BaseModel):
    native_meeting_id: str = Field(..., description="The unique identifier for the meeting (e.g., 'xxx-xxxx-xxx' from Google Meet URL)")
    language: Optional[str] = Field(None, description="Optional language code for transcription (e.g., 'en', 'es'). If not specified, auto-detected")
    bot_name: Optional[str] = Field(None, description="Optional custom name for the bot in the meeting")
    platform: str = Field("google_meet", description="The meeting platform (e.g., 'google_meet', 'zoom'). Default is 'google_meet'.")


class UpdateBotConfig(BaseModel):
    language: str = Field(..., description="New language code for transcription (e.g., 'en', 'es')")


class UpdateMeetingData(BaseModel):
    name: Optional[str] = Field(None, description="Optional meeting name/title")
    participants: Optional[List[str]] = Field(None, description="Optional list of participant names")
    languages: Optional[List[str]] = Field(None, description="Optional list of language codes detected/used in the meeting")
    notes: Optional[str] = Field(None, description="Optional meeting notes or description")


# ---------------------------
# Helper for async requests
# ---------------------------
async def make_request(method: str, url: str, api_key: str, payload: Optional[dict] = None):
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.request(
                method,
                url,
                headers=get_headers(api_key),
                json=payload
            )
            response.raise_for_status()
            return response.json()
    except httpx.HTTPStatusError as http_err:
        return {
            "error": "HTTP error occurred",
            "status_code": http_err.response.status_code,
            "details": http_err.response.text
        }
    except httpx.TimeoutException:
        return {"error": "Request timed out"}
    except httpx.RequestError as req_err:
        return {"error": "Request failed", "details": str(req_err)}
    except Exception as e:
        return {"error": "Unexpected error", "details": str(e)}


# ---------------------------
# Endpoints (docstrings preserved)
# ---------------------------
@app.post("/request-meeting-bot", operation_id="request_meeting_bot")
async def request_meeting_bot(
    data: RequestMeetingBot,
    api_key: str = Depends(get_api_key)
) -> Dict[str, Any]:
    """
    Request a Vexa bot to join a meeting for transcription.
    
    Args:
        native_meeting_id: The unique identifier for the meeting (e.g., 'xxx-xxxx-xxx' from Google Meet URL)
        language: Optional language code for transcription (e.g., 'en', 'es'). If not specified, auto-detected
        bot_name: Optional custom name for the bot in the meeting
        meeting_platform: The meeting platform (e.g., 'google_meet', 'zoom'). Default is 'google_meet'.
    
    Returns:
        JSON string with bot request details and status
    
    Note: After a successful request, it typically takes about 10 seconds for the bot to join the meeting.
    """
    url = f"{BASE_URL}/bots"
    payload = data.dict()
    return await make_request("POST", url, api_key, payload)


@app.get("/meeting-transcript/{meeting_platform}/{meeting_id}", operation_id="get_meeting_transcript")
async def get_meeting_transcript(
    meeting_id: str,
    meeting_platform: str = "google_meet",
    api_key: str = Depends(get_api_key)
) -> Dict[str, Any]:
    """
    Get the real-time transcript for a meeting.
    
    Args:
        meeting_id: The unique identifier for the meeting
        meeting_platform: The meeting platform (e.g., 'google_meet', 'zoom'). Default is 'google_meet'.
    
    Returns:
        JSON with the meeting transcript data including segments with speaker, timestamp, and text
    
    Note: This provides real-time transcription data and can be called during or after the meeting.
    """
    url = f"{BASE_URL}/transcripts/{meeting_platform}/{meeting_id}"
    return await make_request("GET", url, api_key)


@app.get("/bot-status", operation_id="get_bot_status")
async def get_bot_status(api_key: str = Depends(get_api_key)) -> Dict[str, Any]:
    """
    Get the status of currently running bots.
    
    Returns:
        JSON with details about active bots under your API key
    """
    url = f"{BASE_URL}/bots/status"
    return await make_request("GET", url, api_key)


@app.put("/bot-config/{meeting_platform}/{meeting_id}", operation_id="update_bot_config")
async def update_bot_config(
    meeting_id: str,
    data: UpdateBotConfig,
    meeting_platform: str = "google_meet",
    api_key: str = Depends(get_api_key)
) -> Dict[str, Any]:
    """
    Update the configuration of an active bot (e.g., changing the language).
    
    Args:
        meeting_id: The identifier of the meeting with the active bot
        language: New language code for transcription (e.g., 'en', 'es')
        meeting_platform: The meeting platform (e.g., 'google_meet', 'zoom'). Default is 'google_meet'.
    
    Returns:
        JSON indicating whether the update request was accepted
    """
    url = f"{BASE_URL}/bots/{meeting_platform}/{meeting_id}/config"
    return await make_request("PUT", url, api_key, data.dict())


@app.delete("/bot/{meeting_platform}/{meeting_id}", operation_id="stop_bot")
async def stop_bot(
    meeting_id: str,
    meeting_platform: str = "google_meet",
    api_key: str = Depends(get_api_key)
) -> Dict[str, Any]:
    """
    Remove an active bot from a meeting.
    
    Args:
        meeting_id: The identifier of the meeting
        meeting_platform: The meeting platform (e.g., 'google_meet', 'zoom'). Default is 'google_meet'.
    
    Returns:
        JSON confirming the bot removal
    """
    url = f"{BASE_URL}/bots/{meeting_platform}/{meeting_id}"
    return await make_request("DELETE", url, api_key)


@app.get("/meetings", operation_id="list_meetings")
async def list_meetings(api_key: str = Depends(get_api_key)) -> Dict[str, Any]:
    """
    List all meetings associated with your API key.
    
    Returns:
        JSON with a list of meeting records
    """
    url = f"{BASE_URL}/meetings"
    return await make_request("GET", url, api_key)


@app.patch("/meeting/{meeting_platform}/{meeting_id}", operation_id="update_meeting_data")
async def update_meeting_data(
    meeting_id: str,
    data: UpdateMeetingData,
    meeting_platform: str = "google_meet",
    api_key: str = Depends(get_api_key)
) -> Dict[str, Any]:
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
    url = f"{BASE_URL}/meetings/{meeting_platform}/{meeting_id}"
    payload = {"data": {k: v for k, v in data.dict().items() if v is not None}}
    return await make_request("PATCH", url, api_key, payload)


@app.delete("/meeting/{meeting_platform}/{meeting_id}", operation_id="delete_meeting")
async def delete_meeting(
    meeting_id: str,
    meeting_platform: str = "google_meet",
    api_key: str = Depends(get_api_key)
) -> Dict[str, Any]:
    """
    Permanently delete a meeting and all its associated transcripts.
    
    Args:
        meeting_id: The unique identifier of the meeting
        meeting_platform: The meeting platform (e.g., 'google_meet', 'zoom'). Default is 'google_meet'.
    
    Returns:
        JSON with confirmation message
    
    Warning: This action cannot be undone.
    """
    url = f"{BASE_URL}/meetings/{meeting_platform}/{meeting_id}"
    return await make_request("DELETE", url, api_key)


# ---------------------------
# MCP & Server
# ---------------------------
mcp = FastApiMCP(app)
mcp.mount_http()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=18888)
