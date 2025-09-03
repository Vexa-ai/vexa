from typing import List, Optional, Dict, Tuple, Any
from pydantic import BaseModel, Field, EmailStr, validator
from datetime import datetime
from enum import Enum, auto
import re # Import re for native ID validation

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
    def construct_meeting_url(cls, platform_str: str, native_id: str) -> Optional[str]:
        """
        Constructs the full meeting URL from platform and native ID.
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
            elif platform == Platform.ZOOM:
                # Basic validation for Zoom meeting ID (numeric) and optional password
                # Example: "1234567890" or "1234567890?pwd=xyz"
                match = re.fullmatch(r"^(\d{9,11})(?:\?pwd=(.+))?$", native_id)
                if match:
                    zoom_id = match.group(1)
                    pwd = match.group(2)
                    url = f"https://*.zoom.us/j/{zoom_id}" # Domain might vary, use wildcard? Or require specific domain?
                    if pwd:
                        url += f"?pwd={pwd}"
                    return url
                else:
                    return None # Invalid ID format
            elif platform == Platform.TEAMS:
                # Teams URLs are complex and often require context - this is a placeholder
                # Might need more specific parsing or different approach for Teams
                # Assuming native_id might be part of a longer URL or require tenant info.
                # This is a very basic guess and likely needs refinement.
                 if native_id: # Placeholder validation
                    # Cannot reliably construct full Teams URL from just an ID usually
                     # Let's return None indicating we can't construct it reliably here
                     # The bot might handle this differently based on the native_id
                     return None # Cannot reliably construct
                 else:
                     return None
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

    class Config:
        orm_mode = True

class TokenBase(BaseModel):
    user_id: int

class TokenCreate(TokenBase):
    pass

class TokenResponse(TokenBase):
    id: int
    token: str
    created_at: datetime

    class Config:
        orm_mode = True

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
    platform: Platform = Field(..., description="Platform identifier (e.g., 'google_meet', 'zoom')")
    native_meeting_id: str = Field(..., description="The native meeting identifier (e.g., 'abc-defg-hij' for Google Meet, '1234567890?pwd=xyz' for Zoom)")
    # meeting_url field removed

    @validator('platform', pre=True) # pre=True allows validating string before enum conversion
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
    native_meeting_id: str = Field(..., description="The platform-specific ID for the meeting (e.g., Google Meet code, Zoom ID)")
    bot_name: Optional[str] = Field(None, description="Optional name for the bot in the meeting")
    language: Optional[str] = Field(None, description="Optional language code for transcription (e.g., 'en', 'es')")
    task: Optional[str] = Field(None, description="Optional task for the transcription model (e.g., 'transcribe', 'translate')")

    @validator('platform')
    def platform_must_be_valid(cls, v):
        """Validate that the platform is one of the supported platforms"""
        try:
            Platform(v)
            return v
        except ValueError:
            supported = ', '.join([p.value for p in Platform])
            raise ValueError(f"Invalid platform '{v}'. Must be one of: {supported}")

    @validator('language')
    def validate_language(cls, v):
        """Validate that the language code is one of the accepted language codes."""
        if v is not None and v != "" and v not in ACCEPTED_LANGUAGE_CODES:
            raise ValueError(f"Invalid language code '{v}'. Must be one of: {sorted(ACCEPTED_LANGUAGE_CODES)}")
        return v

    @validator('task')
    def validate_task(cls, v):
        """Validate that the task is one of the allowed tasks."""
        if v is not None and v != "" and v not in ALLOWED_TASKS:
            raise ValueError(f"Invalid task '{v}'. Must be one of: {sorted(ALLOWED_TASKS)}")
        return v

class MeetingResponse(BaseModel): # Not inheriting from MeetingBase anymore to avoid duplicate fields if DB model is used directly
    id: int = Field(..., description="Internal database ID for the meeting")
    user_id: int
    platform: Platform # Use the enum type
    native_meeting_id: Optional[str] = Field(None, description="The native meeting identifier provided during creation") # Renamed from platform_specific_id for clarity
    constructed_meeting_url: Optional[str] = Field(None, description="The meeting URL constructed internally, if possible") # Added for info
    status: str
    bot_container_id: Optional[str]
    start_time: Optional[datetime]
    end_time: Optional[datetime]
    data: Optional[Dict] = Field(default_factory=dict, description="JSON data containing meeting metadata like name, participants, languages, and notes")
    created_at: datetime
    updated_at: datetime

    class Config:
        orm_mode = True
        use_enum_values = True # Serialize Platform enum to its string value

# --- Meeting Update Schema ---
class MeetingDataUpdate(BaseModel):
    """Schema for updating meeting data fields - restricted to user-editable fields only"""
    name: Optional[str] = Field(None, description="Meeting name/title")
    participants: Optional[List[str]] = Field(None, description="List of participant names")
    languages: Optional[List[str]] = Field(None, description="List of language codes detected/used in the meeting")
    notes: Optional[str] = Field(None, description="Meeting notes or description")

    @validator('languages')
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

    @validator('language')
    def validate_language(cls, v):
        """Validate that the language code is one of the accepted faster-whisper codes."""
        if v is not None and v != "" and v not in ACCEPTED_LANGUAGE_CODES:
            raise ValueError(f"Invalid language code '{v}'. Must be one of: {sorted(ACCEPTED_LANGUAGE_CODES)}")
        return v

    @validator('task')
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
    created_at: Optional[datetime]
    speaker: Optional[str] = None
    absolute_start_time: Optional[datetime] = Field(None, description="Absolute start timestamp of the segment (UTC)")
    absolute_end_time: Optional[datetime] = Field(None, description="Absolute end timestamp of the segment (UTC)")

    @validator('language')
    def validate_language(cls, v):
        """Validate that the language code is one of the accepted faster-whisper codes."""
        if v is not None and v != "" and v not in ACCEPTED_LANGUAGE_CODES:
            raise ValueError(f"Invalid language code '{v}'. Must be one of: {sorted(ACCEPTED_LANGUAGE_CODES)}")
        return v

    class Config:
        orm_mode = True
        allow_population_by_field_name = True # Allow using both alias and field name

# --- WebSocket Schema (NEW - Represents data from WhisperLive) ---

class WhisperLiveData(BaseModel):
    """Schema for the data message sent by WhisperLive to the collector."""
    uid: str # Unique identifier from the original client connection
    platform: Platform
    meeting_url: Optional[str] = None
    token: str # User API token
    meeting_id: str # Native Meeting ID (string, e.g., 'abc-xyz-pqr')
    segments: List[TranscriptionSegment]

    @validator('platform', pre=True)
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
    # ---
    segments: List[TranscriptionSegment] = Field(..., description="List of transcript segments")

    class Config:
        orm_mode = True # Allows creation from ORM models (e.g., joined query result)
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

    @validator('normalized_status')
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