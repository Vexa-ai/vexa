# vexa_client.py

import requests
from typing import Optional, List, Dict, Any
import os
from urllib.parse import urljoin
import time # Import time for sleep
import re # Import re for parsing meeting ID

# Default Base URL (can be overridden)
DEFAULT_BASE_URL = "http://localhost:8056" 

class VexaClientError(Exception):
    """Custom exception for Vexa client errors."""
    pass

class VexaClient:
    """
    A Python client for interacting with the Vexa API Gateway.
    """

    def __init__(self, 
                 base_url: str = DEFAULT_BASE_URL, 
                 api_key: Optional[str] = None, 
                 admin_key: Optional[str] = None):
        """
        Initializes the Vexa API client.

        Args:
            base_url: The base URL of the Vexa API Gateway.
            api_key: The API key for regular user operations (X-API-Key).
            admin_key: The API key for administrative operations (X-Admin-API-Key).
        """
        # Ensure base_url is a string
        if not isinstance(base_url, str):
            base_url = str(base_url)
        
        self.base_url = base_url
        self._api_key = api_key
        self._admin_key = admin_key
        self._session = requests.Session()

    def _get_headers(self, api_type: str = 'user') -> Dict[str, str]:
        """Prepares headers for the request based on API type."""
        headers = {"Content-Type": "application/json"}
        if api_type == 'admin':
            if not self._admin_key:
                raise VexaClientError("Admin API key is required for this operation but was not provided.")
            headers["X-Admin-API-Key"] = self._admin_key
        elif api_type == 'user':
            if not self._api_key:
                raise VexaClientError("User API key is required for this operation but was not provided.")
            headers["X-API-Key"] = self._api_key
        else:
             raise ValueError("Invalid api_type specified. Use 'user' or 'admin'.")
        return headers

    def _request(self, 
                 method: str, 
                 path: str, 
                 api_type: str = 'user', 
                 params: Optional[Dict[str, Any]] = None, 
                 json_data: Optional[Dict[str, Any]] = None) -> Any:
        """
        Internal helper method to make requests to the API gateway.

        Args:
            method: HTTP method (e.g., 'GET', 'POST', 'DELETE').
            path: API endpoint path (e.g., '/bots').
            api_type: Type of API key required ('user' or 'admin').
            params: Optional dictionary of query parameters.
            json_data: Optional dictionary for the JSON request body.

        Returns:
            The JSON response from the API.

        Raises:
            VexaClientError: If the required API key is missing.
            requests.exceptions.RequestException: For connection or other request errors.
            requests.exceptions.HTTPError: For non-2xx status codes.
        """
        url = urljoin(self.base_url, path)
        headers = self._get_headers(api_type)
        
        # Debug output - print URL and headers for troubleshooting
        print(f"\nDEBUG: Making {method} request to {url}")
        print(f"DEBUG: Headers: {headers}")
        print(f"DEBUG: Params: {params}")
        print(f"DEBUG: JSON data: {json_data}")
        
        try:
            response = self._session.request(
                method=method,
                url=url,
                headers=headers,
                params=params,
                json=json_data
            )
            # Debug response
            print(f"DEBUG: Response status: {response.status_code}")
            print(f"DEBUG: Response headers: {dict(response.headers)}")
            try:
                print(f"DEBUG: Response content: {response.text[:500]}...")
            except:
                print(f"DEBUG: Could not display response content")
                
            response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)
            
            # Handle cases where response might be empty (e.g., 204 No Content)
            if response.status_code == 204:
                return None 
            
            return response.json()
        except requests.exceptions.JSONDecodeError:
            raise VexaClientError(f"Failed to decode JSON response from {method} {url}. Status: {response.status_code}, Body: {response.text}")
        except requests.exceptions.HTTPError as e:
            # Attempt to include API error details if available
            try:
                error_details = e.response.json()
                detail_msg = error_details.get('detail', e.response.text)
            except requests.exceptions.JSONDecodeError:
                detail_msg = e.response.text
            raise VexaClientError(f"HTTP Error {e.response.status_code} for {method} {url}: {detail_msg}") from e
        except requests.exceptions.RequestException as e:
            raise VexaClientError(f"Request failed for {method} {url}: {e}") from e


    # --- Bot Management ---

    def request_bot(self, platform: str, native_meeting_id: str, bot_name: Optional[str] = None, language: Optional[str] = None, task: Optional[str] = None) -> Dict[str, Any]:
        """
        Requests a new bot to join a meeting using platform and native ID.

        Args:
            platform: Platform identifier (e.g., 'google_meet', 'zoom').
            native_meeting_id: The platform-specific meeting identifier.
            bot_name: Optional name for the bot in the meeting.
            language: Optional language code for transcription (e.g., 'en', 'es').
            task: Optional transcription task ('transcribe' or 'translate').

        Returns:
            Dictionary representing the created/updated Meeting object.
        """
        payload = {
            "platform": platform, 
            "native_meeting_id": native_meeting_id
        }
        if bot_name:
            payload["bot_name"] = bot_name
        if language:
            payload["language"] = language
        if task:
            payload["task"] = task
            
        return self._request("POST", "/bots", api_type='user', json_data=payload)

    def stop_bot(self, platform: str, native_meeting_id: str) -> Dict[str, str]:
        """
        Requests a running bot to stop for a specific meeting using platform and native ID.
        The API returns a 202 Accepted response immediately while the stop happens in the background.

        Args:
            platform: Platform identifier (e.g., 'google_meet', 'zoom').
            native_meeting_id: The platform-specific meeting identifier.

        Returns:
            A dictionary containing a confirmation message (e.g., {"message": "..."}).
        """
        path = f"/bots/{platform}/{native_meeting_id}"
        # _request handles 202 status and returns the JSON body
        return self._request("DELETE", path, api_type='user')

    def update_bot_config(self, platform: str, native_meeting_id: str, language: Optional[str] = None, task: Optional[str] = None) -> Dict[str, Any]:
        """
        Updates the configuration (language, task) for an active bot.
        The API returns a 202 Accepted response immediately while the command is sent.

        Args:
            platform: Platform identifier (e.g., 'google_meet').
            native_meeting_id: The platform-specific meeting identifier.
            language: Optional new language code (e.g., 'en', 'es'). Pass None to not update.
            task: Optional new task ('transcribe' or 'translate'). Pass None to not update.

        Returns:
            A dictionary containing a confirmation message (e.g., {"message": "..."}).
        """
        path = f"/bots/{platform}/{native_meeting_id}/config"
        payload = {}
        if language is not None:
            payload["language"] = language
        if task is not None:
            payload["task"] = task
            
        if not payload: # Check if there's anything to update
            raise VexaClientError("No configuration updates provided (language or task must be specified).")
            
        # _request handles 202 status and returns the JSON body
        return self._request("PUT", path, api_type='user', json_data=payload)

    def get_running_bots_status(self) -> List[Dict[str, Any]]:
        """
        Retrieves the status of running bot containers for the authenticated user.

        Returns:
            List of dictionaries, each representing the status of a running bot container.
        """
        response = self._request("GET", "/bots/status", api_type='user')
        # The API returns a dict {"running_bots": [...]}, extract the list.
        return response.get("running_bots", [])

    # --- Transcriptions ---

    def get_meetings(self) -> List[Dict[str, Any]]:
        """
        Retrieves the list of meetings initiated by the user associated with the API key.
        
        Each meeting includes metadata such as:
        - Basic meeting info (id, platform, status, timestamps, etc.)
        - Meeting data (name, participants, languages, notes) in the 'data' field
        - Auto-collected participants and languages (populated when meeting completes)

        Returns:
            List of dictionaries, each representing a Meeting object with the following structure:
            {
                "id": int,
                "platform": str,
                "native_meeting_id": str,
                "status": str,
                "start_time": str (ISO datetime),
                "end_time": str (ISO datetime),
                "data": {
                    "name": str (optional),
                    "participants": List[str] (optional, auto-collected from transcripts),
                    "languages": List[str] (optional, auto-collected from transcripts),  
                    "notes": str (optional)
                },
                "created_at": str (ISO datetime),
                "updated_at": str (ISO datetime),
                ...
            }
        """
        response = self._request("GET", "/meetings", api_type='user')
        # The API returns a dict {"meetings": [...]}, extract the list.
        meetings = response.get("meetings", [])
        
        # Ensure each meeting has a data field (backward compatibility)
        for meeting in meetings:
            if "data" not in meeting:
                meeting["data"] = {}
                
        return meetings

    def get_meeting_by_id(self, platform: str, native_meeting_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieves a specific meeting by platform and native ID from the user's meetings list.
        
        Args:
            platform: Platform identifier (e.g., 'google_meet', 'zoom').
            native_meeting_id: The platform-specific meeting identifier.
            
        Returns:
            Dictionary representing the Meeting object, or None if not found.
        """
        meetings = self.get_meetings()
        for meeting in meetings:
            if (meeting.get("platform") == platform and 
                meeting.get("native_meeting_id") == native_meeting_id):
                return meeting
        return None

    @staticmethod
    def get_meeting_metadata(meeting: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extracts metadata from a meeting object.
        
        Args:
            meeting: Meeting dictionary as returned by get_meetings() or get_meeting_by_id().
            
        Returns:
            Dictionary containing the meeting's metadata (name, participants, languages, notes).
        """
        return meeting.get("data", {})

    @staticmethod
    def get_meeting_participants(meeting: Dict[str, Any]) -> List[str]:
        """
        Extracts participant list from a meeting object.
        
        Args:
            meeting: Meeting dictionary as returned by get_meetings() or get_meeting_by_id().
            
        Returns:
            List of participant names (empty list if none found).
        """
        return meeting.get("data", {}).get("participants", [])

    @staticmethod
    def get_meeting_languages(meeting: Dict[str, Any]) -> List[str]:
        """
        Extracts language list from a meeting object.
        
        Args:
            meeting: Meeting dictionary as returned by get_meetings() or get_meeting_by_id().
            
        Returns:
            List of language codes (empty list if none found).
        """
        return meeting.get("data", {}).get("languages", [])

    def get_transcript(self, platform: str, native_meeting_id: str) -> Dict[str, Any]:
        """
        Retrieves the transcript for a specific meeting using platform and native ID.

        Args:
            platform: Platform identifier (e.g., 'google_meet', 'zoom').
            native_meeting_id: The platform-specific meeting identifier.

        Returns:
            Dictionary containing meeting details and transcript segments.
        """
        path = f"/transcripts/{platform}/{native_meeting_id}"
        return self._request("GET", path, api_type='user')

    def update_meeting_data(self, 
                           platform: str, 
                           native_meeting_id: str,
                           name: Optional[str] = None,
                           participants: Optional[List[str]] = None,
                           languages: Optional[List[str]] = None,
                           notes: Optional[str] = None) -> Dict[str, Any]:
        """
        Updates meeting metadata. Only name, participants, languages, and notes can be updated.

        Args:
            platform: Platform identifier (e.g., 'google_meet', 'zoom').
            native_meeting_id: The platform-specific meeting identifier.
            name: Optional meeting name/title.
            participants: Optional list of participant names.
            languages: Optional list of language codes detected/used in the meeting.
            notes: Optional meeting notes or description.

        Returns:
            Dictionary representing the updated Meeting object.
        """
        # Build the data payload with only provided fields
        data_payload = {}
        if name is not None:
            data_payload["name"] = name
        if participants is not None:
            data_payload["participants"] = participants
        if languages is not None:
            data_payload["languages"] = languages
        if notes is not None:
            data_payload["notes"] = notes
            
        if not data_payload:
            raise VexaClientError("No data fields provided for meeting update.")
            
        payload = {"data": data_payload}
        path = f"/meetings/{platform}/{native_meeting_id}"
        return self._request("PATCH", path, api_type='user', json_data=payload)

    def delete_meeting(self, platform: str, native_meeting_id: str) -> Dict[str, str]:
        """
        Deletes a meeting and all its associated transcripts.
        
        Args:
            platform: Platform identifier (e.g., 'google_meet', 'zoom').
            native_meeting_id: The platform-specific meeting identifier.
            
        Returns:
            Dictionary containing a confirmation message.
        """
        path = f"/meetings/{platform}/{native_meeting_id}"
        return self._request("DELETE", path, api_type='user')

    # --- User Profile ---

    def set_webhook_url(self, webhook_url: str) -> Dict[str, Any]:
        """
        Sets the webhook URL for the authenticated user.

        Args:
            webhook_url: The URL to which webhook notifications should be sent.

        Returns:
            Dictionary representing the updated User object.
        """
        payload = {"webhook_url": webhook_url}
        return self._request("PUT", "/user/webhook", api_type='user', json_data=payload)

    # --- Admin: User Management ---

    def create_user(self, 
                    email: str, 
                    name: Optional[str] = None, 
                    image_url: Optional[str] = None,
                    max_concurrent_bots: Optional[int] = None
                   ) -> Dict[str, Any]:
        """
        Creates a new user (Admin Only).

        Args:
            email: The email address for the new user.
            name: Optional name for the user.
            image_url: Optional URL for the user's image.
            max_concurrent_bots: Optional maximum number of concurrent bots allowed (defaults server-side if None).

        Returns:
            Dictionary representing the created User object.
        """
        payload = {"email": email}
        if name:
            payload["name"] = name
        if image_url:
            payload["image_url"] = image_url
        if max_concurrent_bots is not None:
             payload["max_concurrent_bots"] = max_concurrent_bots
             
        return self._request("POST", "/admin/users", api_type='admin', json_data=payload)

    def list_users(self, skip: int = 0, limit: int = 100) -> List[Dict[str, Any]]:
        """
        Lists users in the system (Admin Only).

        Args:
            skip: Number of users to skip (for pagination).
            limit: Maximum number of users to return (for pagination).

        Returns:
            A list of dictionaries, each representing a User object.
        """
        params = {"skip": skip, "limit": limit}
        return self._request("GET", "/admin/users", api_type='admin', params=params)

    def update_user(self, 
                    user_id: int, 
                    name: Optional[str] = None, 
                    image_url: Optional[str] = None,
                    max_concurrent_bots: Optional[int] = None
                   ) -> Dict[str, Any]:
        """
        Updates specific fields for an existing user (Admin Only).
        Only include parameters for the fields you want to change.

        Args:
            user_id: The ID of the user to update.
            name: Optional new name for the user.
            image_url: Optional new URL for the user's image.
            max_concurrent_bots: Optional new maximum number of concurrent bots.

        Returns:
            Dictionary representing the updated User object.
        """
        payload = {}
        if name is not None:
            payload["name"] = name
        if image_url is not None:
            payload["image_url"] = image_url
        if max_concurrent_bots is not None:
             payload["max_concurrent_bots"] = max_concurrent_bots
             
        if not payload: # Check if any update fields were provided
            raise VexaClientError("No update fields provided for update_user.")
            
        path = f"/admin/users/{user_id}"
        return self._request("PATCH", path, api_type='admin', json_data=payload)

    def get_user_by_email(self, email: str) -> Dict[str, Any]:
        """
        Retrieves a specific user by their email address (Admin Only).

        Args:
            email: The email address of the user to retrieve.

        Returns:
            Dictionary representing the User object.
        """
        path = f"/admin/users/email/{email}"
        return self._request("GET", path, api_type='admin')

    # --- Admin: Token Management ---

    def create_token(self, user_id: int) -> Dict[str, Any]:
        """
        Generates a new API token for a specific user (Admin Only).

        Args:
            user_id: The ID of the user for whom to create the token.

        Returns:
            Dictionary representing the created APIToken object.
        """
        return self._request("POST", f"/admin/users/{user_id}/tokens", api_type='admin')
