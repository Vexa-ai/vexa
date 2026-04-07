from typing import List, Optional, Dict, Tuple, Any
from urllib.parse import urlparse, parse_qs
import hashlib
from pydantic import BaseModel, Field, EmailStr, field_serializer, field_validator, model_validator, ValidationInfo
from datetime import datetime
from enum import Enum, auto
import re # Import re for native ID validation
import logging # Import logging for status validation warnings

# Setup logger for status validation warnings
logger = logging.getLogger(__name__)

# --- Language Codes from faster-whisper ---
# These are the accepted language codes from the faster-whisper library
# Source: faster_whisper.tokenizer._LANGUAGE_CODES
ACCEPTED_LANGUAGE_CODES = {
    "af", "am", "ar", "as", "az", "ba", "be", "bg", "bn", "bo", "br", "bs", "ca", "cs", "cy", 
    "da", "de", "el", "en", "es", "et", "eu", "fa", "fi", "fo", "fr", "gl", "gu", "ha", "haw", 
    "he", "hi", "hr", "ht", "hu", "hy", "id", "is", "it", "ja", "jw", "ka", "kk", "km", "kn", 
    "ko", "la", "lb", "ln", "lo", "lt", "lv", "mg", "mi", "mk", "ml", "mn", "mr", "ms", "mt", 
    "my", "ne", "nl", "nn", "no", "oc", "pa", "pl", "ps", "pt", "ro", "ru", "sa", "sd", "si", 
    "sk", "sl", "sn", "so", "sq", "sr", "su", "sv", "sw", "ta", "te", "tg", "th", "tk", "tl", 
    "tr", "tt", "uk", "ur", "uz", "vi", "yi", "yo", "zh", "yue"
}

# --- Allowed Tasks ---
ALLOWED_TASKS = {"transcribe", "translate"}

# --- Allowed Transcription Tiers ---
ALLOWED_TRANSCRIPTION_TIERS = {"realtime", "deferred"}

# --- Meeting Status Definitions ---

class MeetingStatus(str, Enum):
    """
    Meeting status values with their sources and transitions.
    
    Status Flow:
    requested -> joining -> awaiting_admission -> active -> stopping -> completed
                                    |              |                 \
                                    v              v                  -> failed
                                 failed         failed
    
    Sources:
    - requested: POST bot API (user)
    - joining: bot callback
    - awaiting_admission: bot callback  
    - active: bot callback
    - stopping: user (stop bot API)
    - completed: user, bot callback
    - failed: bot callback, validation errors
    """
    REQUESTED = "requested"
    JOINING = "joining"
    AWAITING_ADMISSION = "awaiting_admission"
    ACTIVE = "active"
    NEEDS_HUMAN_HELP = "needs_human_help"
    STOPPING = "stopping"
    COMPLETED = "completed"
    FAILED = "failed"

class MeetingCompletionReason(str, Enum):
    """
    Reasons for meeting completion.
    """
    STOPPED = "stopped"  # User stopped by API
    VALIDATION_ERROR = "validation_error"  # Post bot validation failed
    AWAITING_ADMISSION_TIMEOUT = "awaiting_admission_timeout"  # Timeout during awaiting admission
    AWAITING_ADMISSION_REJECTED = "awaiting_admission_rejected"  # Rejected during awaiting admission
    LEFT_ALONE = "left_alone"  # Timeout for being alone
    EVICTED = "evicted"  # Kicked out from meeting using meeting UI
    MAX_BOT_TIME_EXCEEDED = "max_bot_time_exceeded"  # Scheduler killed bot after max lifetime

class MeetingFailureStage(str, Enum):
    """
    Stages where meeting can fail.
    """
    REQUESTED = "requested"
    JOINING = "joining"
    AWAITING_ADMISSION = "awaiting_admission"
    ACTIVE = "active"

# --- Status Transition Helpers ---

def get_valid_status_transitions() -> Dict[MeetingStatus, List[MeetingStatus]]:
    """
    Returns valid status transitions for meetings.
    
    Returns:
        Dict mapping current status to list of valid next statuses
    """
    return {
        MeetingStatus.REQUESTED: [
            MeetingStatus.JOINING,
            MeetingStatus.FAILED,
            MeetingStatus.COMPLETED,
            MeetingStatus.STOPPING,
        ],
        MeetingStatus.JOINING: [
            MeetingStatus.AWAITING_ADMISSION,
            MeetingStatus.ACTIVE,  # Allow direct transition when bot is immediately admitted (no waiting room)
            MeetingStatus.NEEDS_HUMAN_HELP,  # Escalation: unknown blocking state during join
            MeetingStatus.FAILED,
            MeetingStatus.COMPLETED,
            MeetingStatus.STOPPING,
        ],
        MeetingStatus.AWAITING_ADMISSION: [
            MeetingStatus.ACTIVE,
            MeetingStatus.NEEDS_HUMAN_HELP,  # Escalation: waiting room timeout approaching
            MeetingStatus.FAILED,
            MeetingStatus.COMPLETED,
            MeetingStatus.STOPPING,
        ],
        MeetingStatus.NEEDS_HUMAN_HELP: [
            MeetingStatus.ACTIVE,     # User resolved, bot continues
            MeetingStatus.FAILED,     # User gave up or VNC timeout
            MeetingStatus.STOPPING,   # User stops bot
            MeetingStatus.COMPLETED,  # User stops bot
        ],
        MeetingStatus.ACTIVE: [
            MeetingStatus.STOPPING,
            MeetingStatus.COMPLETED,
            MeetingStatus.FAILED,
        ],
        MeetingStatus.STOPPING: [
            MeetingStatus.COMPLETED,
            MeetingStatus.FAILED,
        ],
        MeetingStatus.COMPLETED: [],  # Terminal state
        MeetingStatus.FAILED: [],  # Terminal state
    }

def is_valid_status_transition(from_status: MeetingStatus, to_status: MeetingStatus) -> bool:
    """
    Check if a status transition is valid.
    
    Args:
        from_status: Current meeting status
        to_status: Desired new status
        
    Returns:
        True if transition is valid, False otherwise
    """
    valid_transitions = get_valid_status_transitions()
    return to_status in valid_transitions.get(from_status, [])

def get_status_source(from_status: MeetingStatus, to_status: MeetingStatus) -> str:
    """
    Get the source that should trigger this status transition.
    
    Args:
        from_status: Current meeting status
        to_status: Desired new status
        
    Returns:
        Source description ("user", "bot_callback", "validation_error")
    """
    # User-controlled transitions (via API)
    if to_status in (MeetingStatus.STOPPING, MeetingStatus.COMPLETED):
        return "user"  # Stop bot API initiated
    
    # Bot callback transitions
    bot_callback_transitions = [
        (MeetingStatus.REQUESTED, MeetingStatus.JOINING),
        (MeetingStatus.JOINING, MeetingStatus.AWAITING_ADMISSION),
        (MeetingStatus.AWAITING_ADMISSION, MeetingStatus.ACTIVE),
        (MeetingStatus.ACTIVE, MeetingStatus.COMPLETED),
        (MeetingStatus.STOPPING, MeetingStatus.COMPLETED),
        (MeetingStatus.REQUESTED, MeetingStatus.FAILED),
        (MeetingStatus.JOINING, MeetingStatus.FAILED),
        (MeetingStatus.AWAITING_ADMISSION, MeetingStatus.FAILED),
        (MeetingStatus.ACTIVE, MeetingStatus.FAILED),
        (MeetingStatus.STOPPING, MeetingStatus.FAILED),
        # Escalation transitions
        (MeetingStatus.JOINING, MeetingStatus.NEEDS_HUMAN_HELP),
        (MeetingStatus.AWAITING_ADMISSION, MeetingStatus.NEEDS_HUMAN_HELP),
        (MeetingStatus.NEEDS_HUMAN_HELP, MeetingStatus.ACTIVE),
        (MeetingStatus.NEEDS_HUMAN_HELP, MeetingStatus.FAILED),
    ]
    
    if (from_status, to_status) in bot_callback_transitions:
        return "bot_callback"
    
    # Validation error transitions
    if to_status == MeetingStatus.FAILED and from_status == MeetingStatus.REQUESTED:
        return "validation_error"
    
    return "unknown"

# --- Platform Definitions ---

class Platform(str, Enum):
    """
    Platform identifiers for meeting platforms.
    The value is the external API name, while the bot_name is what's used internally by the bot.
    """
    GOOGLE_MEET = "google_meet"
    ZOOM = "zoom"
    TEAMS = "teams"
    BROWSER_SESSION = "browser_session"
    
    @property
    def bot_name(self) -> str:
        """
        Returns the platform name used by the bot containers.
        This maps external API platform names to internal bot platform names.
        """
        mapping = {
            Platform.GOOGLE_MEET: "google_meet",
            Platform.ZOOM: "zoom",
            Platform.TEAMS: "teams"
        }
        return mapping[self]
    
    @classmethod
    def get_bot_name(cls, platform_str: str) -> str:
        """
        Static method to get the bot platform name from a string.
        This is useful when you have a platform string but not a Platform instance.
        
        Args:
            platform_str: The platform identifier string (e.g., 'google_meet')
            
        Returns:
            The platform name used by the bot (e.g., 'google')
        """
        try:
            platform = Platform(platform_str)
            return platform.bot_name
        except ValueError:
            # If the platform string is invalid, return it unchanged or handle error
            return platform_str # Or raise error/log warning

    @classmethod
    def get_api_value(cls, bot_platform_name: str) -> Optional[str]:
        """
        Gets the external API enum value from the internal bot platform name.
        Returns None if the bot name is unknown.
        """
        reverse_mapping = {
            "google_meet": Platform.GOOGLE_MEET.value,
            "zoom": Platform.ZOOM.value,
            "teams": Platform.TEAMS.value
        }
        return reverse_mapping.get(bot_platform_name)

    @classmethod
    def construct_meeting_url(
        cls,
        platform_str: str,
        native_id: str,
        passcode: Optional[str] = None,
        base_host: Optional[str] = None,
    ) -> Optional[str]:
        """
        Constructs the full meeting URL from platform, native ID, and optional passcode.
        Returns None if the platform is unknown, ID is invalid, or the ID is a hex hash
        (indicating the caller should use the raw meeting_url field instead).

        Args:
            base_host: Optional override for the Teams hostname
                       (e.g. 'teams.microsoft.com' for enterprise short URLs).
                       Defaults to 'teams.live.com'.
        """
        try:
            platform = Platform(platform_str)
            if platform == Platform.GOOGLE_MEET:
                # Accept standard abc-defg-hij format and custom Workspace nicknames
                if re.fullmatch(r"^[a-z]{3}-[a-z]{4}-[a-z]{3}$", native_id) or \
                   re.fullmatch(r"^[a-z0-9][a-z0-9-]{3,38}[a-z0-9]$", native_id):
                    return f"https://meet.google.com/{native_id}"
                return None
            elif platform == Platform.TEAMS:
                # Hex hash = long legacy URL; caller must use raw meeting_url field
                if re.fullmatch(r"^[0-9a-f]{16}$", native_id):
                    return None
                if re.fullmatch(r"^\d{10,15}$", native_id):
                    host = base_host or "teams.live.com"
                    url = f"https://{host}/meet/{native_id}"
                    if passcode:
                        url += f"?p={passcode}"
                    return url
                return None
            elif platform == Platform.ZOOM:
                # Zoom meeting ID (numeric, 9-11 digits) and optional passcode
                if re.fullmatch(r"^\d{9,11}$", native_id):
                    base_url = f"https://zoom.us/j/{native_id}"
                    if passcode:
                        return f"{base_url}?pwd={passcode}"
                    return base_url
                return None
            elif platform == Platform.BROWSER_SESSION:
                # Browser sessions use opaque IDs (UUIDs, etc.) — no meeting URL to construct
                if native_id:
                    return f"browser_session://{native_id}"
                return None
            else:
                return None
        except ValueError:
            return None

# --- Schemas from Admin API --- 

class UserBase(BaseModel): # Base for common user fields
    email: EmailStr
    name: Optional[str] = None
    image_url: Optional[str] = None
    max_concurrent_bots: Optional[int] = Field(None, description="Maximum number of concurrent bots allowed for the user")
    data: Optional[Dict[str, Any]] = Field(None, description="JSONB storage for arbitrary user data, like webhook URLs")

class UserCreate(UserBase):
    pass

class UserResponse(UserBase):
    id: int
    created_at: datetime
    max_concurrent_bots: int = Field(..., description="Maximum number of concurrent bots allowed for the user")

    @field_serializer('data')
    def exclude_webhook_secret(self, data: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Exclude webhook_secret from API responses for security."""
        if data is None:
            return None
        return {k: v for k, v in data.items() if k != 'webhook_secret'}

    class Config:
        from_attributes = True

class TokenBase(BaseModel):
    user_id: int

class TokenCreate(TokenBase):
    pass

class TokenResponse(TokenBase):
    id: int
    token: str
    scopes: List[str] = []
    name: Optional[str] = None
    created_at: datetime
    last_used_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None

    class Config:
        from_attributes = True

class UserDetailResponse(UserResponse):
    api_tokens: List[TokenResponse] = []

# --- ADD UserUpdate Schema for PATCH ---
class UserUpdate(BaseModel):
    email: Optional[EmailStr] = None # Make all fields optional for PATCH
    name: Optional[str] = None
    image_url: Optional[str] = None
    max_concurrent_bots: Optional[int] = Field(None, description="Maximum number of concurrent bots allowed for the user")
    data: Optional[Dict[str, Any]] = Field(None, description="JSONB storage for arbitrary user data, like webhook URLs and subscription info")
# --- END UserUpdate Schema ---

# --- Meeting Schemas --- 

class MeetingBase(BaseModel):
    platform: Platform = Field(..., description="Platform identifier (e.g., 'google_meet', 'teams')")
    native_meeting_id: str = Field(..., description="The native meeting identifier (e.g., 'abc-defg-hij' for Google Meet, '1234567890' for Teams)")
    # meeting_url field removed

    @field_validator('platform', mode='before') # mode='before' allows validating string before enum conversion
    @classmethod
    def validate_platform_str(cls, v):
        """Validate that the platform string is one of the supported platforms"""
        try:
            Platform(v)
            return v
        except ValueError:
            supported = ', '.join([p.value for p in Platform])
            raise ValueError(f"Invalid platform '{v}'. Must be one of: {supported}")

    # Removed get_bot_platform method, use Platform.get_bot_name(self.platform.value) if needed

class AutomaticLeave(BaseModel):
    """Optional overrides for automatic-leave timeouts (milliseconds).

    New field names: max_wait_for_admission, max_time_left_alone, max_bot_time.
    Old field names (waiting_room_timeout, everyone_left_timeout) still accepted
    for backward compatibility via model_validator.
    """
    model_config = {"extra": "forbid"}

    max_bot_time: Optional[int] = Field(None, description="Absolute max bot lifetime in ms (server-enforced via scheduler)")
    max_wait_for_admission: Optional[int] = Field(None, description="Max time to wait for admission in ms")
    max_time_left_alone: Optional[int] = Field(None, description="Max time left alone before leaving in ms")
    no_one_joined_timeout: Optional[int] = Field(None, description="No one joined timeout in ms")
    # Old names kept as aliases for backward compatibility (D1)
    waiting_room_timeout: Optional[int] = Field(None, description="[DEPRECATED] Use max_wait_for_admission")
    everyone_left_timeout: Optional[int] = Field(None, description="[DEPRECATED] Use max_time_left_alone")

    @model_validator(mode='after')
    def merge_deprecated_aliases(self):
        """Map old field names to new ones. Old names only used if new names not set."""
        if self.waiting_room_timeout is not None and self.max_wait_for_admission is None:
            self.max_wait_for_admission = self.waiting_room_timeout
        if self.everyone_left_timeout is not None and self.max_time_left_alone is None:
            self.max_time_left_alone = self.everyone_left_timeout
        return self


_TEAMS_ENTERPRISE_HOSTS = {
    "teams.microsoft.com",
    "gov.teams.microsoft.us",
    "dod.teams.microsoft.us",
}


def _is_teams_host(host: str) -> bool:
    return host in _TEAMS_ENTERPRISE_HOSTS or host.endswith(".teams.microsoft.us") or host.endswith(".teams.microsoft.com")


def parse_meeting_url(url: str) -> dict:
    """Parse a meeting URL into platform, native_meeting_id, passcode, etc.

    Returns a dict with keys: platform, native_meeting_id, passcode, meeting_url, teams_base_host.
    Raises ValueError on unrecognised or invalid URLs.
    """
    url = (url or "").strip()
    if not url:
        raise ValueError("meeting_url cannot be empty")

    # Handle msteams: deep links by converting to https for urlparse
    parse_url = url
    if url.lower().startswith("msteams:"):
        parse_url = "https://teams.microsoft.com" + url[len("msteams:"):]

    parsed = urlparse(parse_url)
    host = (parsed.hostname or "").lower()
    path = parsed.path or ""
    query = parse_qs(parsed.query or "")

    # Google Meet
    if host == "meet.google.com":
        if path.startswith("/lookup/"):
            raise ValueError("Google Meet /lookup/ URLs cannot be joined directly.")
        code = path.strip("/").split("/")[0] if path else ""
        if re.fullmatch(r"[a-z]{3}-[a-z]{4}-[a-z]{3}", code):
            return {"platform": "google_meet", "native_meeting_id": code}
        if re.fullmatch(r"[a-z0-9][a-z0-9-]{3,38}[a-z0-9]", code):
            return {"platform": "google_meet", "native_meeting_id": code}
        raise ValueError("Invalid Google Meet URL: expected https://meet.google.com/abc-defg-hij")

    # Teams personal (teams.live.com/meet/<digits>?p=<passcode>)
    if host.endswith("teams.live.com"):
        m = re.match(r"^/meet/(\d{10,15})/?$", path)
        if not m:
            raise ValueError("Unsupported teams.live.com URL format. Expected /meet/<10-15 digit id>.")
        return {
            "platform": "teams",
            "native_meeting_id": m.group(1),
            "passcode": (query.get("p") or [None])[0],
        }

    # Teams enterprise
    if _is_teams_host(host):
        # Deep link: /v2/?meetingjoin=true#/meet/<id>?p=<passcode>
        fragment = parsed.fragment or ""
        if path.rstrip("/") in ("/v2", "") and fragment.startswith("/meet/"):
            frag_parsed = urlparse("https://x" + fragment)
            fm = re.match(r"^/meet/(\d{10,15})/?$", frag_parsed.path)
            if fm:
                frag_query = parse_qs(frag_parsed.query or "")
                return {
                    "platform": "teams",
                    "native_meeting_id": fm.group(1),
                    "passcode": (frag_query.get("p") or [None])[0],
                    "teams_base_host": host,
                }

        # Short URL: /meet/<numeric_id>?p=<passcode>
        m = re.match(r"^/meet/(\d{10,15})/?$", path)
        if m:
            return {
                "platform": "teams",
                "native_meeting_id": m.group(1),
                "passcode": (query.get("p") or [None])[0],
                "teams_base_host": host,
            }

        # Long legacy URL: /l/meetup-join/...
        if "/l/meetup-join/" in path:
            url_hash = hashlib.sha256(url.encode()).hexdigest()[:16]
            return {
                "platform": "teams",
                "native_meeting_id": url_hash,
                "meeting_url": url,
            }
        raise ValueError("Unsupported Teams URL format. Expected /meet/<id>?p=<passcode> or /l/meetup-join/...")

    # Zoom
    if "zoom.us" in host or "zoomgov.com" in host:
        parts = [p for p in path.split("/") if p]
        native_id = ""
        if len(parts) >= 2 and parts[0] in {"j", "w"}:
            native_id = parts[1]
        elif len(parts) >= 3 and parts[0] == "wc" and parts[1] == "join":
            native_id = parts[2]
        if not re.fullmatch(r"\d{9,11}", native_id or ""):
            raise ValueError("Unsupported Zoom URL format. Expected https://zoom.us/j/<9-11 digit id>.")
        return {
            "platform": "zoom",
            "native_meeting_id": native_id,
            "passcode": (query.get("pwd") or [None])[0],
        }

    raise ValueError("Unsupported meeting URL (unknown provider).")


class MeetingCreate(BaseModel):
    model_config = {"extra": "ignore"}

    platform: Optional[Platform] = Field(None, description="Meeting platform. Required unless agent_enabled=true with no meeting.")
    native_meeting_id: Optional[str] = Field(None, description="The platform-specific ID for the meeting (e.g., Google Meet code, Teams ID). Required unless agent_enabled=true with no meeting.")
    bot_name: Optional[str] = Field(None, description="Optional name for the bot in the meeting")
    language: Optional[str] = Field(None, description="Optional language code for transcription (e.g., 'en', 'es')")
    task: Optional[str] = Field(None, description="Optional task for the transcription model (e.g., 'transcribe', 'translate')")
    transcription_tier: Optional[str] = Field(
        "realtime",
        description="Transcription priority tier: 'realtime' (default) or 'deferred'"
    )
    recording_enabled: Optional[bool] = Field(
        None,
        description="Optional per-meeting override for recording persistence (true/false)."
    )
    transcribe_enabled: Optional[bool] = Field(
        None,
        description="Optional per-meeting override for transcription processing (true/false)."
    )
    passcode: Optional[str] = Field(None, description="Optional passcode for the meeting (Teams only)")
    meeting_url: Optional[str] = Field(
        None,
        description="Meeting URL. When provided without native_meeting_id, the URL is parsed to extract platform, native_meeting_id, and passcode automatically. Supports Google Meet, Teams (all formats), and Zoom URLs."
    )
    teams_base_host: Optional[str] = Field(
        None,
        description="Internal: Teams hostname for short enterprise URLs (e.g. 'teams.microsoft.com', 'gov.teams.microsoft.us'). Populated automatically by the MCP parser."
    )
    zoom_obf_token: Optional[str] = Field(
        None,
        description="Optional one-time Zoom OBF token. If omitted for Zoom meetings, the backend will mint one from the user's stored Zoom OAuth connection."
    )
    voice_agent_enabled: Optional[bool] = Field(
        False,
        description="Enable voice agent (TTS, chat, screen share, avatar streaming) capabilities for this meeting"
    )
    default_avatar_url: Optional[str] = Field(
        None,
        description="Custom default avatar image URL for the bot's camera feed. Shown when no screen content is active. If omitted, the default Vexa logo is used."
    )
    automatic_leave: Optional[AutomaticLeave] = Field(
        None,
        description="Optional overrides for automatic-leave timeouts (ms). Unset fields keep defaults."
    )
    agent_enabled: Optional[bool] = Field(
        False,
        description="Enable Claude agent in the bot container. Agent can control the browser, debug selectors, and modify code interactively."
    )
    mode: Optional[str] = Field(
        None,
        description="Bot mode: 'browser_session' for remote browser access, or None for default meeting mode."
    )
    video: Optional[bool] = Field(
        True,
        description="Enable video recording. When true, automatically sets recording_enabled=true and capture_modes=['audio', 'video']. Default: on."
    )
    authenticated: Optional[bool] = Field(
        False,
        description="Use stored browser userdata for authenticated join. Requires prior browser_session setup."
    )
    # Workspace fields — used by browser_session mode for git workspace setup
    workspaceGitRepo: Optional[str] = Field(None, description="Git repo URL for workspace setup in browser_session mode")
    workspaceGitToken: Optional[str] = Field(None, description="Git token for workspace repo access")
    workspaceGitBranch: Optional[str] = Field(None, description="Git branch for workspace (default: main)")

    @field_validator('platform')
    @classmethod
    def platform_must_be_valid(cls, v):
        """Validate that the platform is one of the supported platforms"""
        if v is None:
            return v  # Allowed when agent_enabled=True with no meeting
        try:
            Platform(v)
            return v
        except ValueError:
            supported = ', '.join([p.value for p in Platform])
            raise ValueError(f"Invalid platform '{v}'. Must be one of: {supported}")

    @field_validator('passcode')
    @classmethod
    def validate_passcode(cls, v, info: ValidationInfo):
        """Validate passcode usage based on platform"""
        platform = info.data.get('platform') if info.data else None
        if platform == Platform.TEAMS:
            if not v or v == "":
                raise ValueError("Passcode is required for Teams meetings. Without it, bots cannot join (lobby rejects anonymous guests).")
            if not re.match(r'^[A-Za-z0-9]{4,20}$', v):
                raise ValueError("Teams passcode must be 4-20 alphanumeric characters")
        elif platform == Platform.GOOGLE_MEET and v is not None and v != "":
            raise ValueError("Passcode is not supported for Google Meet meetings")
        return v

    @field_validator('zoom_obf_token')
    @classmethod
    def validate_zoom_obf_token(cls, v, info: ValidationInfo):
        """Validate OBF token usage based on platform."""
        if v is not None and v != "":
            platform = info.data.get('platform') if info.data else None
            if platform != Platform.ZOOM:
                raise ValueError("zoom_obf_token is only supported for Zoom meetings")
        return v

    @field_validator('language')
    @classmethod
    def validate_language(cls, v):
        """Validate that the language code is one of the accepted language codes."""
        if v is not None and v != "" and v not in ACCEPTED_LANGUAGE_CODES:
            raise ValueError(f"Invalid language code '{v}'. Must be one of: {sorted(ACCEPTED_LANGUAGE_CODES)}")
        return v

    @field_validator('task')
    @classmethod
    def validate_task(cls, v):
        """Validate that the task is one of the allowed tasks."""
        if v is not None and v != "" and v not in ALLOWED_TASKS:
            raise ValueError(f"Invalid task '{v}'. Must be one of: {sorted(ALLOWED_TASKS)}")
        return v

    @field_validator('transcription_tier')
    @classmethod
    def validate_transcription_tier(cls, v):
        """Validate transcription tier."""
        if v is None or v == "":
            return "realtime"
        normalized = str(v).strip().lower()
        if normalized not in ALLOWED_TRANSCRIPTION_TIERS:
            raise ValueError(
                f"Invalid transcription_tier '{v}'. Must be one of: {sorted(ALLOWED_TRANSCRIPTION_TIERS)}"
            )
        return normalized

    @field_validator('native_meeting_id')
    @classmethod
    def validate_native_meeting_id(cls, v, info: ValidationInfo):
        """Validate that the native meeting ID matches the expected format for the platform."""
        if v is None:
            return v  # Allowed when agent_enabled=True with no meeting
        if not v.strip():
            raise ValueError("native_meeting_id cannot be empty")

        platform = info.data.get('platform') if info.data else None
        if not platform:
            return v  # Let platform validator handle this case
        
        platform = Platform(platform)
        native_id = v.strip()
        
        if platform == Platform.GOOGLE_MEET:
            # Google Meet format: standard abc-defg-hij OR custom Workspace nickname (5-40 alphanumeric/hyphen)
            if not re.fullmatch(r"^[a-z]{3}-[a-z]{4}-[a-z]{3}$", native_id) and \
               not re.fullmatch(r"^[a-z0-9][a-z0-9-]{3,38}[a-z0-9]$", native_id):
                raise ValueError("Google Meet ID must be in format 'abc-defg-hij' or a custom nickname (5-40 lowercase alphanumeric/hyphen chars)")

        elif platform == Platform.TEAMS:
            # Reject full URLs up front
            if native_id.startswith(('http://', 'https://', 'teams.')):
                raise ValueError("Teams meeting ID must be the numeric ID or hash, not a full URL")
            # Accept numeric ID (10-15 digits) or 16-char hex hash (for legacy /l/meetup-join/ URLs)
            if not re.fullmatch(r"^\d{10,15}$", native_id) and \
               not re.fullmatch(r"^[0-9a-f]{16}$", native_id):
                raise ValueError(
                    "Teams native_meeting_id must be a 10-15 digit numeric ID "
                    "or a 16-character hex hash (for legacy /l/meetup-join/ URLs)"
                )
        
        return v

    @field_validator('mode')
    @classmethod
    def validate_mode(cls, v):
        """Validate that mode is a supported value."""
        if v is not None and v not in ('browser_session',):
            raise ValueError(f"Invalid mode '{v}'. Must be one of: 'browser_session'")
        return v

    @model_validator(mode='before')
    @classmethod
    def parse_meeting_url_if_provided(cls, data: Any) -> Any:
        """When meeting_url is provided but native_meeting_id is missing, parse the URL."""
        if not isinstance(data, dict):
            return data
        url = data.get("meeting_url")
        if not url or data.get("native_meeting_id"):
            return data
        try:
            parsed = parse_meeting_url(url)
            if parsed.get("platform") and not data.get("platform"):
                data["platform"] = parsed["platform"]
            if parsed.get("native_meeting_id"):
                data["native_meeting_id"] = parsed["native_meeting_id"]
            if parsed.get("passcode") and not data.get("passcode"):
                data["passcode"] = parsed["passcode"]
            if parsed.get("meeting_url"):
                data["meeting_url"] = parsed["meeting_url"]
            if parsed.get("teams_base_host") and not data.get("teams_base_host"):
                data["teams_base_host"] = parsed["teams_base_host"]
        except ValueError:
            pass  # let the downstream validators produce the error
        return data

    @model_validator(mode='after')
    def validate_meeting_or_agent(self):
        """Ensure at least one of meeting info, agent_enabled, or browser_session mode is provided."""
        has_meeting = self.platform is not None and self.native_meeting_id is not None
        has_agent = bool(self.agent_enabled)
        has_browser_session = self.mode == "browser_session"
        if not has_meeting and not has_agent and not has_browser_session:
            raise ValueError("Either provide platform + native_meeting_id for a meeting, set agent_enabled=true, or set mode='browser_session'")
        # Teams requires passcode — without it bots can't pass the lobby
        if has_meeting and self.platform == Platform.TEAMS and not self.meeting_url and not self.passcode:
            raise ValueError("Teams meetings require a passcode. Without it, bots cannot join (lobby rejects anonymous guests). Provide the 'passcode' field.")
        return self

class MeetingResponse(BaseModel): # Not inheriting from MeetingBase anymore to avoid duplicate fields if DB model is used directly
    id: int = Field(..., description="Internal database ID for the meeting")
    user_id: int
    platform: Optional[str] = None  # str to allow "agent" for agent-only containers
    native_meeting_id: Optional[str] = Field(None, description="The native meeting identifier provided during creation") # Renamed from platform_specific_id for clarity
    constructed_meeting_url: Optional[str] = Field(None, description="The meeting URL constructed internally, if possible") # Added for info
    status: MeetingStatus = Field(..., description="Current meeting status")
    bot_container_id: Optional[str]
    start_time: Optional[datetime]
    end_time: Optional[datetime]
    data: Optional[Dict] = Field(default_factory=dict, description="JSON data containing meeting metadata like name, participants, languages, notes, and status reasons")
    created_at: datetime
    updated_at: datetime

    @field_validator('status', mode='before')
    @classmethod
    def normalize_status(cls, v):
        """Normalize invalid status values to valid enum values"""
        if isinstance(v, str):
            # Try to use the value as-is first
            try:
                return MeetingStatus(v)
            except ValueError:
                # For unknown status values, default to 'completed' as a safe fallback
                logger.warning("Unknown meeting status '%s' → completed", v)
                return MeetingStatus.COMPLETED
        
        return v

    @field_validator('data')
    @classmethod
    def validate_status_data(cls, v, info: ValidationInfo):
        """Validate that status-related data is consistent with meeting status."""
        if v is None:
            return v
            
        status = info.data.get('status') if info.data else None
        if not status:
            return v
            
        # Validate completion reasons
        if status == MeetingStatus.COMPLETED:
            reason = v.get('completion_reason')
            if reason and reason not in [r.value for r in MeetingCompletionReason]:
                raise ValueError(f"Invalid completion_reason '{reason}'. Must be one of: {[r.value for r in MeetingCompletionReason]}")
        
        # Validate failure stage
        elif status == MeetingStatus.FAILED:
            stage = v.get('failure_stage')
            if stage and stage not in [s.value for s in MeetingFailureStage]:
                raise ValueError(f"Invalid failure_stage '{stage}'. Must be one of: {[s.value for s in MeetingFailureStage]}")
        
        return v

    @field_serializer('data')
    def exclude_webhook_secret_from_data(self, data: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Exclude webhook_secret from API responses for security."""
        if data is None:
            return None
        return {k: v for k, v in data.items() if k != 'webhook_secret'}

    class Config:
        from_attributes = True
        use_enum_values = True # Serialize Platform enum to its string value

# --- Meeting Update Schema ---
class MeetingDataUpdate(BaseModel):
    """Schema for updating meeting data fields - restricted to user-editable fields only"""
    name: Optional[str] = Field(None, description="Meeting name/title")
    participants: Optional[List[str]] = Field(None, description="List of participant names")
    languages: Optional[List[str]] = Field(None, description="List of language codes detected/used in the meeting")
    notes: Optional[str] = Field(None, description="Meeting notes or description")

    @field_validator('languages')
    @classmethod
    def validate_languages(cls, v):
        """Validate that all language codes in the list are accepted faster-whisper codes."""
        if v is not None:
            invalid_languages = [lang for lang in v if lang not in ACCEPTED_LANGUAGE_CODES]
            if invalid_languages:
                raise ValueError(f"Invalid language codes: {invalid_languages}. Must be one of: {sorted(ACCEPTED_LANGUAGE_CODES)}")
        return v

class MeetingUpdate(BaseModel):
    """Schema for updating meeting data via PATCH requests"""
    data: MeetingDataUpdate = Field(..., description="Meeting metadata to update")

# --- Bot Configuration Update Schema ---
class MeetingConfigUpdate(BaseModel):
    """Schema for updating bot configuration (language, task, and allowed languages)"""
    language: Optional[str] = Field(None, description="New language code (e.g., 'en', 'es')")
    task: Optional[str] = Field(None, description="New task ('transcribe' or 'translate')")
    allowed_languages: Optional[list[str]] = Field(None, description="Whitelist of allowed language codes. Whisper auto-detects, result discarded if not in list. Single entry forces that language.")

    @field_validator('language')
    @classmethod
    def validate_language(cls, v):
        """Validate that the language code is one of the accepted faster-whisper codes."""
        if v is not None and v != "" and v not in ACCEPTED_LANGUAGE_CODES:
            raise ValueError(f"Invalid language code '{v}'. Must be one of: {sorted(ACCEPTED_LANGUAGE_CODES)}")
        return v

    @field_validator('allowed_languages')
    @classmethod
    def validate_allowed_languages(cls, v):
        """Validate that all language codes in the whitelist are accepted."""
        if v is not None:
            for code in v:
                if code not in ACCEPTED_LANGUAGE_CODES:
                    raise ValueError(f"Invalid language code '{code}' in allowed_languages. Must be one of: {sorted(ACCEPTED_LANGUAGE_CODES)}")
        return v

    @field_validator('task')
    @classmethod
    def validate_task(cls, v):
        """Validate that the task is one of the allowed tasks."""
        if v is not None and v != "" and v not in ALLOWED_TASKS:
            raise ValueError(f"Invalid task '{v}'. Must be one of: {sorted(ALLOWED_TASKS)}")
        return v

# --- Transcription Schemas --- 

class TranscriptionSegment(BaseModel):
    # id: Optional[int] # No longer relevant to expose outside DB
    start_time: float = Field(..., alias='start') # Add alias
    end_time: float = Field(..., alias='end')     # Add alias
    text: str
    language: Optional[str]
    created_at: Optional[datetime] = Field(default=None)
    speaker: Optional[str] = None
    # Segments are marked completed/partial. This is important for real-time UI updates
    # (e.g., to show when a partial segment becomes "confirmed" via SAME_OUTPUT_THRESHOLD).
    completed: Optional[bool] = None
    absolute_start_time: Optional[datetime] = Field(None, description="Absolute start timestamp of the segment (UTC)")
    absolute_end_time: Optional[datetime] = Field(None, description="Absolute end timestamp of the segment (UTC)")
    segment_id: Optional[str] = Field(None, description="Stable segment identity from bot")

    @field_validator('language')
    @classmethod
    def validate_language(cls, v):
        """Validate that the language code is one of the accepted faster-whisper codes."""
        if v is not None and v != "" and v not in ACCEPTED_LANGUAGE_CODES:
            raise ValueError(f"Invalid language code '{v}'. Must be one of: {sorted(ACCEPTED_LANGUAGE_CODES)}")
        return v

    class Config:
        from_attributes = True
        populate_by_name = True # Allow using both alias and field name

# --- Other Schemas ---
class TranscriptionResponse(BaseModel): # Doesn't inherit MeetingResponse to avoid redundancy if joining data
    """Response for getting a meeting's transcript."""
    # Meeting details (consider duplicating fields from MeetingResponse or nesting)
    id: int = Field(..., description="Internal database ID for the meeting")
    platform: Platform
    native_meeting_id: Optional[str]
    constructed_meeting_url: Optional[str]
    status: str
    start_time: Optional[datetime]
    end_time: Optional[datetime]
    recordings: List[Dict[str, Any]] = Field(default_factory=list, description="Recording metadata attached to the meeting (if available).")
    notes: Optional[str] = Field(None, description="Meeting notes (from meeting data, if provided).")
    data: Optional[Dict[str, Any]] = Field(None, description="Meeting data (mode, session_token, etc.)")
    # ---
    segments: List[TranscriptionSegment] = Field(..., description="List of transcript segments")

    class Config:
        from_attributes = True # Allows creation from ORM models (e.g., joined query result)
        use_enum_values = True

# --- Utility Schemas --- 

class HealthResponse(BaseModel):
    status: str
    redis: str
    database: str
    stream: Optional[str] = None
    timestamp: datetime

class ErrorResponse(BaseModel):
    detail: str # Standard FastAPI error response uses 'detail'

class MeetingListResponse(BaseModel):
    meetings: List[MeetingResponse] 

# --- ADD Bot Status Schemas ---
class BotStatus(BaseModel):
    container_id: Optional[str] = None
    container_name: Optional[str] = None
    platform: Optional[str] = None
    native_meeting_id: Optional[str] = None
    status: Optional[str] = None
    normalized_status: Optional[str] = None
    created_at: Optional[str] = None
    start_time: Optional[str] = None
    labels: Optional[Dict[str, str]] = None
    meeting_id_from_name: Optional[str] = None
    meeting_status: Optional[str] = None
    data: Optional[Dict[str, Any]] = None

    @field_validator('normalized_status')
    @classmethod
    def validate_normalized_status(cls, v):
        if v is None:
            return v
        allowed = {
            'Requested',
            'Starting',
            'Up',
            'Stopping',
            'Exited',
            'Failed'
        }
        if v not in allowed:
            raise ValueError(f"normalized_status must be one of {sorted(allowed)}")
        return v

class BotStatusResponse(BaseModel):
    running_bots: List[BotStatus]
# --- END Bot Status Schemas ---

# --- Analytics Schemas ---
class UserTableResponse(BaseModel):
    """User data for analytics table - excludes sensitive fields"""
    id: int
    email: str
    name: Optional[str]
    image_url: Optional[str]
    created_at: datetime
    max_concurrent_bots: int
    # Excludes: data, api_tokens

    class Config:
        from_attributes = True

class MeetingTableResponse(BaseModel):
    """Meeting data for analytics table - excludes sensitive fields"""
    id: int
    user_id: int
    platform: Platform
    native_meeting_id: Optional[str]
    status: MeetingStatus
    start_time: Optional[datetime]
    end_time: Optional[datetime]
    created_at: datetime
    updated_at: datetime
    # Excludes: data, transcriptions, sessions

    @field_validator('status', mode='before')
    @classmethod
    def normalize_status(cls, v):
        """Normalize invalid status values to valid enum values"""
        if isinstance(v, str):
            # Try to use the value as-is first
            try:
                return MeetingStatus(v)
            except ValueError:
                # For unknown status values, default to 'completed' as a safe fallback
                logger.warning("Unknown meeting status '%s' → completed", v)
                return MeetingStatus.COMPLETED
        
        return v

    class Config:
        from_attributes = True
        use_enum_values = True

class MeetingSessionResponse(BaseModel):
    """Meeting session data for telematics"""
    id: int
    meeting_id: int
    session_uid: str
    session_start_time: datetime

    class Config:
        from_attributes = True

class TranscriptionStats(BaseModel):
    """Transcription statistics for a meeting"""
    total_transcriptions: int
    total_duration: float
    unique_speakers: int
    languages_detected: List[str]

class MeetingPerformanceMetrics(BaseModel):
    """Performance metrics for a meeting"""
    join_time: Optional[float]  # seconds to join
    admission_time: Optional[float]  # seconds to get admitted
    total_duration: Optional[float]  # meeting duration in seconds
    bot_uptime: Optional[float]  # bot uptime in seconds

class MeetingTelematicsResponse(BaseModel):
    """Comprehensive telematics data for a specific meeting"""
    meeting: MeetingResponse
    sessions: List[MeetingSessionResponse]
    transcription_stats: Optional[TranscriptionStats]
    performance_metrics: Optional[MeetingPerformanceMetrics]

class UserMeetingStats(BaseModel):
    """User meeting statistics"""
    total_meetings: int
    completed_meetings: int
    failed_meetings: int
    active_meetings: int
    total_duration: Optional[float]  # total meeting duration in seconds
    average_duration: Optional[float]  # average meeting duration in seconds

class UserUsagePatterns(BaseModel):
    """User usage patterns"""
    most_used_platform: Optional[str]
    meetings_per_day: float
    peak_usage_hours: List[int]  # hours of day (0-23)
    last_activity: Optional[datetime]

class UserAnalyticsResponse(BaseModel):
    """Comprehensive user analytics data including full user record"""
    user: UserDetailResponse  # This includes the data field
    meeting_stats: UserMeetingStats
    usage_patterns: UserUsagePatterns
    api_tokens: Optional[List[TokenResponse]]  # Optional for security
# --- END Analytics Schemas ---

# --- Recording Schemas ---

class RecordingStatus(str, Enum):
    IN_PROGRESS = "in_progress"
    UPLOADING = "uploading"
    COMPLETED = "completed"
    FAILED = "failed"

class RecordingSource(str, Enum):
    BOT = "bot"
    UPLOAD = "upload"
    URL = "url"

class MediaFileType(str, Enum):
    AUDIO = "audio"
    VIDEO = "video"
    SCREENSHOT = "screenshot"

class MediaFileResponse(BaseModel):
    id: int
    type: MediaFileType
    format: str
    storage_backend: str
    file_size_bytes: Optional[int] = None
    duration_seconds: Optional[float] = None
    metadata: Optional[Dict[str, Any]] = Field(None, validation_alias="extra_metadata")
    created_at: datetime

    class Config:
        from_attributes = True
        use_enum_values = True
        populate_by_name = True

class RecordingResponse(BaseModel):
    id: int
    meeting_id: Optional[int] = None
    user_id: int
    session_uid: Optional[str] = None
    source: RecordingSource
    status: RecordingStatus
    error_message: Optional[str] = None
    created_at: datetime
    completed_at: Optional[datetime] = None
    media_files: List[MediaFileResponse] = Field(default_factory=list)

    class Config:
        from_attributes = True
        use_enum_values = True

class RecordingListResponse(BaseModel):
    recordings: List[RecordingResponse]
# --- END Recording Schemas ---


# --- Voice Agent / Meeting Interaction Schemas ---

class SpeakRequest(BaseModel):
    """Request to make the bot speak in the meeting."""
    text: Optional[str] = Field(None, description="Text to speak (bot does TTS)")
    audio_url: Optional[str] = Field(None, description="URL to pre-rendered audio file")
    audio_base64: Optional[str] = Field(None, description="Base64-encoded audio data")
    format: Optional[str] = Field("wav", description="Audio format: wav, mp3, pcm, opus")
    sample_rate: Optional[int] = Field(24000, description="Sample rate for PCM audio (Hz)")
    provider: Optional[str] = Field("piper", description="TTS provider: piper (default, local), openai, cartesia, elevenlabs")
    voice: Optional[str] = Field("alloy", description="Voice ID for TTS")

    @field_validator('text', 'audio_url', 'audio_base64')
    @classmethod
    def at_least_one_source(cls, v, info: ValidationInfo):
        """At least one of text, audio_url, or audio_base64 must be provided."""
        return v

class ChatSendRequest(BaseModel):
    """Request to send a message to the meeting chat."""
    text: str = Field(..., description="Message text to send in the meeting chat")

class ChatMessage(BaseModel):
    """A chat message from the meeting."""
    sender: str
    text: str
    timestamp: float
    is_from_bot: bool = False

class ChatMessagesResponse(BaseModel):
    """Response with captured chat messages."""
    messages: List[ChatMessage]

class ScreenContentRequest(BaseModel):
    """Request to show content on screen (via screen share)."""
    type: str = Field(..., description="Content type: image, video, url, html")
    url: Optional[str] = Field(None, description="URL of the content to display")
    html: Optional[str] = Field(None, description="Custom HTML content to display")
    start_share: bool = Field(True, description="Auto-start screen sharing")

# --- END Voice Agent Schemas ---
