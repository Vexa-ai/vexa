from typing import List, Optional, Dict, Tuple, Any
from pydantic import BaseModel, Field, EmailStr, field_serializer, field_validator, ValidationInfo
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
# These are the tasks supported by WhisperLive
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
            MeetingStatus.FAILED,
            MeetingStatus.COMPLETED,
            MeetingStatus.STOPPING,
        ],
        MeetingStatus.AWAITING_ADMISSION: [
            MeetingStatus.ACTIVE,
            MeetingStatus.FAILED,
            MeetingStatus.COMPLETED,
            MeetingStatus.STOPPING,
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
    def construct_meeting_url(cls, platform_str: str, native_id: str, passcode: Optional[str] = None) -> Optional[str]:
        """
        Constructs the full meeting URL from platform, native ID, and optional passcode.
        Returns None if the platform is unknown or ID is invalid for the platform.
        """
        try:
            platform = Platform(platform_str)
            if platform == Platform.GOOGLE_MEET:
                # Basic validation for Google Meet code format (xxx-xxxx-xxx)
                if re.fullmatch(r"^[a-z]{3}-[a-z]{4}-[a-z]{3}$", native_id):
                     return f"https://meet.google.com/{native_id}"
                else:
                     return None # Invalid ID format
            elif platform == Platform.TEAMS:
                # Teams meeting ID (numeric) and optional passcode
                # Only accept numeric meeting IDs, not full URLs
                if re.fullmatch(r"^\d{10,15}$", native_id):
                    url = f"https://teams.live.com/meet/{native_id}"
                    if passcode:
                        url += f"?p={passcode}"
                    return url
                else:
                    return None # Invalid Teams ID format - must be numeric only
            elif platform == Platform.ZOOM:
                # Zoom meeting ID (numeric, 10-11 digits) and optional passcode
                if re.fullmatch(r"^\d{10,11}$", native_id):
                    base_url = f"https://zoom.us/j/{native_id}"
                    if passcode:
                        return f"{base_url}?pwd={passcode}"
                    return base_url
                else:
                    return None # Invalid Zoom ID format - must be numeric
            else:
                return None # Unknown platform
        except ValueError:
            return None # Invalid platform string

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
    created_at: datetime

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

class MeetingCreate(BaseModel):
    platform: Platform
    native_meeting_id: str = Field(..., description="The platform-specific ID for the meeting (e.g., Google Meet code, Teams ID)")
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
    zoom_obf_token: Optional[str] = Field(
        None,
        description="Optional one-time Zoom OBF token. If omitted for Zoom meetings, the backend will mint one from the user's stored Zoom OAuth connection."
    )
    voice_agent_enabled: Optional[bool] = Field(
        True,
        description="Enable voice agent (TTS, chat, screen share) capabilities for this meeting"
    )
    default_avatar_url: Optional[str] = Field(
        None,
        description="Custom default avatar image URL for the bot's camera feed. Shown when no screen content is active. If omitted, the default Vexa logo is used."
    )

    @field_validator('platform')
    @classmethod
    def platform_must_be_valid(cls, v):
        """Validate that the platform is one of the supported platforms"""
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
        if v is not None and v != "":
            platform = info.data.get('platform') if info.data else None
            if platform == Platform.GOOGLE_MEET:
                raise ValueError("Passcode is not supported for Google Meet meetings")
            elif platform == Platform.TEAMS:
                # Teams passcode validation (alphanumeric, reasonable length)
                if not re.match(r'^[A-Za-z0-9]{8,20}$', v):
                    raise ValueError("Teams passcode must be 8-20 alphanumeric characters")
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
        if not v or not v.strip():
            raise ValueError("native_meeting_id cannot be empty")
        
        platform = info.data.get('platform') if info.data else None
        if not platform:
            return v  # Let platform validator handle this case
        
        platform = Platform(platform)
        native_id = v.strip()
        
        if platform == Platform.GOOGLE_MEET:
            # Google Meet format: abc-defg-hij
            if not re.fullmatch(r"^[a-z]{3}-[a-z]{4}-[a-z]{3}$", native_id):
                raise ValueError("Google Meet ID must be in format 'abc-defg-hij' (lowercase letters only)")
        
        elif platform == Platform.TEAMS:
            # Teams format: numeric ID only (10-15 digits)
            if not re.fullmatch(r"^\d{10,15}$", native_id):
                raise ValueError("Teams meeting ID must be 10-15 digits only (not a full URL)")
            
            # Explicitly reject full URLs
            if native_id.startswith(('http://', 'https://', 'teams.microsoft.com', 'teams.live.com')):
                raise ValueError("Teams meeting ID must be the numeric ID only (e.g., '9399697580372'), not a full URL")
        
        return v

class MeetingResponse(BaseModel): # Not inheriting from MeetingBase anymore to avoid duplicate fields if DB model is used directly
    id: int = Field(..., description="Internal database ID for the meeting")
    user_id: int
    platform: Platform # Use the enum type
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
    """Schema for updating bot configuration (language and task)"""
    language: Optional[str] = Field(None, description="New language code (e.g., 'en', 'es')")
    task: Optional[str] = Field(None, description="New task ('transcribe' or 'translate')")

    @field_validator('language')
    @classmethod
    def validate_language(cls, v):
        """Validate that the language code is one of the accepted faster-whisper codes."""
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

# --- Transcription Schemas --- 

class TranscriptionSegment(BaseModel):
    # id: Optional[int] # No longer relevant to expose outside DB
    start_time: float = Field(..., alias='start') # Add alias
    end_time: float = Field(..., alias='end')     # Add alias
    text: str
    language: Optional[str]
    created_at: Optional[datetime] = Field(default=None)
    speaker: Optional[str] = None
    # WhisperLive marks segments as completed/partial. This is important for real-time UI updates
    # (e.g., to show when a partial segment becomes "confirmed" via SAME_OUTPUT_THRESHOLD).
    completed: Optional[bool] = None
    absolute_start_time: Optional[datetime] = Field(None, description="Absolute start timestamp of the segment (UTC)")
    absolute_end_time: Optional[datetime] = Field(None, description="Absolute end timestamp of the segment (UTC)")

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

# --- WebSocket Schema (NEW - Represents data from WhisperLive) ---

class WhisperLiveData(BaseModel):
    """Schema for the data message sent by WhisperLive to the collector."""
    uid: str # Unique identifier from the original client connection
    platform: Platform
    meeting_url: Optional[str] = None
    token: str # User API token
    meeting_id: str # Native Meeting ID (string, e.g., 'abc-xyz-pqr')
    segments: List[TranscriptionSegment]

    @field_validator('platform', mode='before')
    @classmethod
    def validate_whisperlive_platform_str(cls, v):
        """Validate that the platform string is one of the supported platforms"""
        try:
            Platform(v)
            return v
        except ValueError:
            supported = ', '.join([p.value for p in Platform])
            raise ValueError(f"Invalid platform '{v}'. Must be one of: {supported}")

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
    labels: Optional[Dict[str, str]] = None
    meeting_id_from_name: Optional[str] = None # Example auxiliary info

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
    provider: Optional[str] = Field("openai", description="TTS provider: openai, cartesia, elevenlabs")
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
