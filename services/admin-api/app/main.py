import logging
import secrets
import string
import os
from fastapi import FastAPI, APIRouter, Depends, HTTPException, status, Security, Response
from fastapi.security import APIKeyHeader
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload, attributes
from typing import List, Optional  # Import List for response model
from datetime import datetime # Import datetime
from sqlalchemy import func
from pydantic import BaseModel, Field, HttpUrl

# Import shared models and schemas
from shared_models.models import User, APIToken, Base, Meeting, Transcription, MeetingSession # Import Base for init_db and Meeting
from shared_models.schemas import (UserCreate, UserResponse, TokenResponse, UserDetailResponse, UserBase, UserUpdate, MeetingResponse,
                                 UserTableResponse, MeetingTableResponse, MeetingSessionResponse, TranscriptionStats, 
                                 MeetingPerformanceMetrics, MeetingTelematicsResponse, UserMeetingStats, 
                                 UserUsagePatterns, UserAnalyticsResponse) # Import analytics schemas

# Database utilities (needs to be created)
from shared_models.database import get_db, init_db  # New import
from shared_models.webhook_url import validate_webhook_url

# Logging configuration
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("admin_api")

# App initialization
app = FastAPI(title="Vexa Admin API")

# --- Pydantic Schemas for new endpoint ---
class WebhookUpdate(BaseModel):
    webhook_url: HttpUrl
    webhook_secret: Optional[str] = Field(None, max_length=512)

class MeetingUserStat(MeetingResponse): # Inherit from MeetingResponse to get meeting fields
    user: UserResponse # Embed UserResponse

class PaginatedMeetingUserStatResponse(BaseModel):
    total: int
    items: List[MeetingUserStat]

# Security - Reuse logic from bot-manager/auth.py for admin token verification
API_KEY_HEADER = APIKeyHeader(name="X-Admin-API-Key", auto_error=False) # Use a distinct header
USER_API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False) # For user-facing endpoints
ADMIN_API_TOKEN = os.getenv("ADMIN_API_TOKEN") # Read from environment

async def verify_admin_token(admin_api_key: str = Security(API_KEY_HEADER)):
    """Dependency to verify the admin API token."""
    if not ADMIN_API_TOKEN:
        logger.error("CRITICAL: ADMIN_API_TOKEN environment variable not set!")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Admin authentication is not configured on the server."
        )
    
    if not admin_api_key or admin_api_key != ADMIN_API_TOKEN:
        logger.warning(f"Invalid admin token provided.")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or missing admin token."
        )
    logger.info("Admin token verified successfully.")
    # No need to return anything, just raises exception on failure 

async def get_current_user(api_key: str = Security(USER_API_KEY_HEADER), db: AsyncSession = Depends(get_db)) -> User:
    """Dependency to verify user API key and return user object."""
    if not api_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing API Key")

    result = await db.execute(
        select(APIToken).where(APIToken.token == api_key).options(selectinload(APIToken.user))
    )
    db_token = result.scalars().first()

    if not db_token or not db_token.user:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid API Key")
    
    return db_token.user

# Router setup (all routes require admin token verification)
admin_router = APIRouter(
    prefix="/admin",
    tags=["Admin"],
    dependencies=[Depends(verify_admin_token)]
)

# New router for user-facing actions
user_router = APIRouter(
    prefix="/user",
    tags=["User"],
    dependencies=[Depends(get_current_user)]
)

# --- Helper Functions --- 
def generate_secure_token(length=40):
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for i in range(length))

# --- User Endpoints ---
@user_router.put("/webhook",
             response_model=UserResponse,
             summary="Set user webhook URL",
             description="Set a webhook URL for the authenticated user to receive notifications.")
async def set_user_webhook(
    webhook_update: WebhookUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Updates the webhook_url and optional webhook_secret for the currently authenticated user.
    Stored in the user's 'data' JSONB field.
    """
    # SSRF validation: block internal/private URLs
    try:
        validate_webhook_url(str(webhook_update.webhook_url))
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        ) from e

    if user.data is None:
        user.data = {}

    user.data['webhook_url'] = str(webhook_update.webhook_url)

    # Only update webhook_secret when explicitly provided (backward compatible: clients
    # that only send webhook_url leave existing secret unchanged)
    if 'webhook_secret' in webhook_update.model_dump(exclude_unset=True):
        if webhook_update.webhook_secret is None or webhook_update.webhook_secret.strip() == '':
            user.data.pop('webhook_secret', None)
        else:
            user.data['webhook_secret'] = webhook_update.webhook_secret.strip()

    # Flag the 'data' field as modified for SQLAlchemy to detect the change
    attributes.flag_modified(user, "data")

    db.add(user)
    await db.commit()
    await db.refresh(user)

    # Fix: Ensure created_at is never None before validation
    if user.created_at is None:
        # Use timezone-naive datetime for TIMESTAMP WITHOUT TIME ZONE column
        user.created_at = datetime.utcnow().replace(tzinfo=None)
        logger.warning(f"created_at was None for user {user.id}, setting to current time")
        db.add(user)
        await db.commit()

    logger.info(f"Updated webhook URL for user {user.email}")

    return UserResponse.model_validate(user)

# --- Admin Endpoints (Copied and adapted from bot-manager/admin.py) --- 
@admin_router.post("/users",
             response_model=UserResponse,
             status_code=status.HTTP_201_CREATED,
             summary="Find or create a user by email",
             responses={
                 status.HTTP_200_OK: {
                     "description": "User found and returned",
                     "model": UserResponse,
                 },
                 status.HTTP_201_CREATED: {
                     "description": "User created successfully",
                     "model": UserResponse,
                 }
             })
async def create_user(user_in: UserCreate, response: Response, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == user_in.email))
    existing_user = result.scalars().first()

    if existing_user:
        logger.info(f"Found existing user: {existing_user.email} (ID: {existing_user.id})")
        # Fix: Ensure created_at is never None before validation
        if existing_user.created_at is None:
            # Use timezone-naive datetime for TIMESTAMP WITHOUT TIME ZONE column
            existing_user.created_at = datetime.utcnow().replace(tzinfo=None)
            logger.warning(f"created_at was None for user {existing_user.id}, setting to current time")
            db.add(existing_user)
            await db.commit()
        response.status_code = status.HTTP_200_OK
        return UserResponse.model_validate(existing_user)

    user_data = user_in.model_dump()
    db_user = User(
        email=user_data['email'],
        name=user_data.get('name'),
        image_url=user_data.get('image_url'),
        max_concurrent_bots=user_data.get('max_concurrent_bots', 0)
    )
    db.add(db_user)
    await db.commit()
    await db.refresh(db_user)
    logger.info(f"Admin created user: {db_user.email} (ID: {db_user.id})")
    # Fix: Set created_at if it's None (server_default may not be loaded after refresh)
    # This happens because server_default values aren't always loaded by refresh()
    if db_user.created_at is None:
        # Use timezone-naive datetime for TIMESTAMP WITHOUT TIME ZONE column
        db_user.created_at = datetime.utcnow().replace(tzinfo=None)
        logger.warning(f"created_at was None for user {db_user.id}, setting to current time")
        db.add(db_user)
        await db.commit()
    return UserResponse.model_validate(db_user)

@admin_router.get("/users", 
            response_model=List[UserResponse], # Use List import
            summary="List all users")
async def list_users(skip: int = 0, limit: int = 100, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).offset(skip).limit(limit))
    users = result.scalars().all()
    
    # Fix: Ensure created_at is never None before validation
    needs_commit = False
    for user in users:
        if user.created_at is None:
            # Use timezone-naive datetime for TIMESTAMP WITHOUT TIME ZONE column
            user.created_at = datetime.utcnow().replace(tzinfo=None)
            logger.warning(f"created_at was None for user {user.id}, setting to current time")
            db.add(user)
            needs_commit = True
    
    # Commit any fixes we made
    if needs_commit:
        await db.commit()
    
    return [UserResponse.model_validate(u) for u in users]

@admin_router.get("/users/email/{user_email}",
            response_model=UserResponse, # Changed from UserDetailResponse
            summary="Get a specific user by email") # Removed ', including their API tokens'
async def get_user_by_email(user_email: str, db: AsyncSession = Depends(get_db)):
    """Gets a user by their email.""" # Removed ', eagerly loading their API tokens.'
    # Removed .options(selectinload(User.api_tokens))
    result = await db.execute(
        select(User)
        .where(User.email == user_email)
    )
    user = result.scalars().first()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )

    # Fix: Ensure created_at is never None before validation
    if user.created_at is None:
        # Use timezone-naive datetime for TIMESTAMP WITHOUT TIME ZONE column
        user.created_at = datetime.utcnow().replace(tzinfo=None)
        logger.warning(f"created_at was None for user {user.id}, setting to current time")
        db.add(user)
        await db.commit()

    # Return the user object. Pydantic will handle serialization using UserDetailResponse.
    return user

@admin_router.get("/users/{user_id}", 
            response_model=UserDetailResponse, # Use the detailed response schema
            summary="Get a specific user by ID, including their API tokens")
async def get_user(user_id: int, db: AsyncSession = Depends(get_db)):
    """Gets a user by their ID, eagerly loading their API tokens."""
    # Eagerly load the api_tokens relationship
    result = await db.execute(
        select(User)
        .where(User.id == user_id)
        .options(selectinload(User.api_tokens))
    )
    user = result.scalars().first()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, 
            detail="User not found"
        )
    
    # Fix: Ensure created_at is never None before validation
    if user.created_at is None:
        # Use timezone-naive datetime for TIMESTAMP WITHOUT TIME ZONE column
        user.created_at = datetime.utcnow().replace(tzinfo=None)
        logger.warning(f"created_at was None for user {user.id}, setting to current time")
        db.add(user)
        await db.commit()
        
    # Return the user object. Pydantic will handle serialization using UserDetailResponse.
    return user

@admin_router.patch("/users/{user_id}",
             response_model=UserResponse,
             summary="Update user details",
             description="Update user's name, image URL, max concurrent bots, or data.")
async def update_user(user_id: int, user_update: UserUpdate, db: AsyncSession = Depends(get_db)):
    """
    Updates specific fields of a user.
    Only provide the fields you want to change in the request body.
    Requires admin privileges.
    """
    print(f"=== ADMIN PATCH USER {user_id} CALLED ===")
    
    # Fetch the user to update
    result = await db.execute(select(User).where(User.id == user_id))
    db_user = result.scalars().first()

    if not db_user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    # Get the update data, excluding unset fields to only update provided values
    update_data = user_update.model_dump(exclude_unset=True)
    print(f"=== Raw update_data: {update_data} ===")
    logger.info(f"Admin PATCH for user {user_id}. Raw update_data: {update_data}")

    # Prevent changing email via this endpoint (if desired)
    if 'email' in update_data and update_data['email'] != db_user.email:
         raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot change user email via this endpoint.")
    elif 'email' in update_data:
         del update_data['email'] # Don't attempt to update email to the same value

    # Handle data field specially for JSONB
    updated = False
    if 'data' in update_data:
        new_data = update_data.pop('data')  # Remove from update_data to handle separately
        if new_data is not None:
            logger.info(f"Admin updating data field for user ID: {user_id}. Current: {db_user.data}, New: {new_data}")
            
            # Replace the data field entirely (rather than merging)
            db_user.data = new_data
            
            # Flag the 'data' field as modified for SQLAlchemy to detect the change
            attributes.flag_modified(db_user, "data")
            updated = True
            logger.info(f"Admin updated data field for user ID: {user_id}")
    else:
        logger.info(f"Admin PATCH for user {user_id}: 'data' not in update_data keys: {list(update_data.keys())}")

    # Update the remaining user object attributes
    for key, value in update_data.items():
        if hasattr(db_user, key) and getattr(db_user, key) != value:
            setattr(db_user, key, value)
            updated = True
            logger.info(f"Admin updated {key} for user ID: {user_id}")

    logger.info(f"Admin update for user ID: {user_id}, updated: {updated}")

    # If any changes were made, commit them
    if updated:
        try:
            await db.commit()
            await db.refresh(db_user)
            logger.info(f"Admin updated user ID: {user_id}")
        except Exception as e: # Catch potential DB errors (e.g., constraints)
            await db.rollback()
            logger.error(f"Error updating user {user_id}: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to update user.")
    else:
        logger.info(f"Admin attempted update for user ID: {user_id}, but no changes detected.")

    # Fix: Ensure created_at is never None before validation
    if db_user.created_at is None:
        # Use timezone-naive datetime for TIMESTAMP WITHOUT TIME ZONE column
        db_user.created_at = datetime.utcnow().replace(tzinfo=None)
        logger.warning(f"created_at was None for user {db_user.id}, setting to current time")
        db.add(db_user)
        await db.commit()

    return UserResponse.model_validate(db_user)

@admin_router.post("/users/{user_id}/tokens", 
             response_model=TokenResponse,
             status_code=status.HTTP_201_CREATED,
             summary="Generate a new API token for a user")
async def create_token_for_user(user_id: int, db: AsyncSession = Depends(get_db)):
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    
    token_value = generate_secure_token()
    # Use the APIToken model from shared_models
    # Use timezone-naive datetime for TIMESTAMP WITHOUT TIME ZONE column
    db_token = APIToken(
        token=token_value, 
        user_id=user_id,
        created_at=datetime.utcnow().replace(tzinfo=None)
    )
    db.add(db_token)
    await db.commit()
    await db.refresh(db_token)
    logger.info(f"Admin created token for user {user_id} ({user.email})")
    # Use TokenResponse for consistency with schema definition (datetime object)
    return TokenResponse.model_validate(db_token)

@admin_router.delete("/tokens/{token_id}", 
                status_code=status.HTTP_204_NO_CONTENT,
                summary="Revoke/Delete an API token by its ID")
async def delete_token(token_id: int, db: AsyncSession = Depends(get_db)):
    """Deletes an API token by its database ID."""
    # Fetch the token by its primary key ID
    db_token = await db.get(APIToken, token_id)
    
    if not db_token:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, 
            detail="Token not found"
        )
        
    # Delete the token
    await db.delete(db_token)
    await db.commit()
    logger.info(f"Admin deleted token ID: {token_id}")
    # No body needed for 204 response
    return 

# --- Usage Stats Endpoints ---
@admin_router.get("/stats/meetings-users",
            response_model=PaginatedMeetingUserStatResponse,
            summary="Get paginated list of meetings joined with users")
async def list_meetings_with_users(
    skip: int = 0, 
    limit: int = 100, 
    db: AsyncSession = Depends(get_db)
):
    """
    Retrieves a paginated list of all meetings, with user details embedded.
    This provides a comprehensive overview for administrators.
    """
    # First, get the total count of meetings for pagination headers
    count_result = await db.execute(select(func.count(Meeting.id)))
    total = count_result.scalar_one()

    # Then, fetch the paginated list of meetings, joining with users
    result = await db.execute(
        select(Meeting)
        .options(selectinload(Meeting.user))
        .order_by(Meeting.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    meetings = result.scalars().all()

    # Now, construct the response using Pydantic models
    response_items = [
        MeetingUserStat(
            **{k: v for k, v in meeting.__dict__.items() if k != "user" and not k.startswith("_")},
            user=UserResponse.model_validate(meeting.user)
        )
        for meeting in meetings if meeting.user
    ]
        
    return PaginatedMeetingUserStatResponse(total=total, items=response_items)

# --- Analytics Endpoints ---
@admin_router.get("/analytics/users",
                  response_model=List[UserTableResponse],
                  summary="Get users table structure without sensitive data")
async def get_users_table(
    skip: int = 0, 
    limit: int = 1000,
    db: AsyncSession = Depends(get_db)
):
    """
    Returns user table data for analytics without exposing sensitive information.
    Excludes: data JSONB field, API tokens
    """
    result = await db.execute(select(User).offset(skip).limit(limit))
    users = result.scalars().all()
    
    # Fix: Ensure created_at is never None before validation
    needs_commit = False
    for user in users:
        if user.created_at is None:
            # Use timezone-naive datetime for TIMESTAMP WITHOUT TIME ZONE column
            user.created_at = datetime.utcnow().replace(tzinfo=None)
            logger.warning(f"created_at was None for user {user.id}, setting to current time")
            db.add(user)
            needs_commit = True
    
    # Commit any fixes we made
    if needs_commit:
        await db.commit()
    
    return [UserTableResponse.model_validate(u) for u in users]

@admin_router.get("/analytics/meetings",
                  response_model=List[MeetingTableResponse], 
                  summary="Get meetings table structure without sensitive data")
async def get_meetings_table(
    skip: int = 0,
    limit: int = 1000, 
    db: AsyncSession = Depends(get_db)
):
    """
    Returns meeting table data for analytics without exposing sensitive information.
    Excludes: data JSONB field, transcriptions content
    """
    result = await db.execute(select(Meeting).offset(skip).limit(limit))
    meetings = result.scalars().all()
    return [MeetingTableResponse.model_validate(m) for m in meetings]

@admin_router.get("/analytics/meetings/{meeting_id}/telematics",
                  response_model=MeetingTelematicsResponse,
                  summary="Get detailed telematics data for a specific meeting")
async def get_meeting_telematics(
    meeting_id: int,
    include_transcriptions: bool = False,
    include_sessions: bool = True,
    db: AsyncSession = Depends(get_db)
):
    """
    Returns comprehensive telematics data for a specific meeting including:
    - Meeting metadata and status
    - Session information
    - Transcription statistics (optional)
    - Performance metrics
    """
    # Get the meeting
    result = await db.execute(select(Meeting).where(Meeting.id == meeting_id))
    meeting = result.scalars().first()
    
    if not meeting:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")
    
    # Get sessions if requested
    sessions = []
    if include_sessions:
        sessions_result = await db.execute(
            select(MeetingSession).where(MeetingSession.meeting_id == meeting_id)
        )
        sessions = sessions_result.scalars().all()
    
    # Calculate transcription stats if requested
    transcription_stats = None
    if include_transcriptions:
        transcriptions_result = await db.execute(
            select(Transcription).where(Transcription.meeting_id == meeting_id)
        )
        transcriptions = transcriptions_result.scalars().all()
        
        if transcriptions:
            total_duration = sum(t.end_time - t.start_time for t in transcriptions)
            unique_speakers = len(set(t.speaker for t in transcriptions if t.speaker))
            languages_detected = list(set(t.language for t in transcriptions if t.language))
            
            transcription_stats = TranscriptionStats(
                total_transcriptions=len(transcriptions),
                total_duration=total_duration,
                unique_speakers=unique_speakers,
                languages_detected=languages_detected
            )
    
    # Calculate performance metrics
    performance_metrics = None
    if meeting.start_time and meeting.end_time:
        total_duration = (meeting.end_time - meeting.start_time).total_seconds()
        performance_metrics = MeetingPerformanceMetrics(
            total_duration=total_duration,
            # Additional metrics can be calculated from meeting.data if available
            join_time=meeting.data.get('join_time') if meeting.data else None,
            admission_time=meeting.data.get('admission_time') if meeting.data else None,
            bot_uptime=meeting.data.get('bot_uptime') if meeting.data else None
        )
    
    return MeetingTelematicsResponse(
        meeting=MeetingResponse.model_validate(meeting),
        sessions=[MeetingSessionResponse.model_validate(s) for s in sessions],
        transcription_stats=transcription_stats,
        performance_metrics=performance_metrics
    )

@admin_router.get("/analytics/users/{user_id}/details",
                  response_model=UserAnalyticsResponse,
                  summary="Get comprehensive user analytics data including full user record")
async def get_user_details(
    user_id: int,
    include_meetings: bool = True,
    include_tokens: bool = False,
    db: AsyncSession = Depends(get_db)
):
    """
    Returns full user record with analytics data including:
    - Complete user profile information (including data JSONB field)
    - Meeting statistics and history
    - Usage patterns
    - API token information (optional)
    """
    # Always eagerly load api_tokens to avoid MissingGreenlet when Pydantic validates UserDetailResponse
    query = select(User).options(selectinload(User.api_tokens))

    result = await db.execute(query.where(User.id == user_id))
    user = result.scalars().first()
    
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    
    # Fix: Ensure created_at is never None before validation
    if user.created_at is None:
        # Use timezone-naive datetime for TIMESTAMP WITHOUT TIME ZONE column
        user.created_at = datetime.utcnow().replace(tzinfo=None)
        logger.warning(f"created_at was None for user {user.id}, setting to current time")
        db.add(user)
        await db.commit()
    
    # Calculate meeting stats
    meetings_result = await db.execute(select(Meeting).where(Meeting.user_id == user_id))
    meetings = meetings_result.scalars().all()
    
    total_meetings = len(meetings)
    completed_meetings = len([m for m in meetings if m.status == 'completed'])
    failed_meetings = len([m for m in meetings if m.status == 'failed'])
    active_meetings = len([m for m in meetings if m.status in ['requested', 'joining', 'awaiting_admission', 'active']])
    
    # Calculate duration stats
    completed_with_duration = [m for m in meetings if m.status == 'completed' and m.start_time and m.end_time]
    total_duration = sum((m.end_time - m.start_time).total_seconds() for m in completed_with_duration) if completed_with_duration else None
    average_duration = total_duration / len(completed_with_duration) if completed_with_duration else None
    
    meeting_stats = UserMeetingStats(
        total_meetings=total_meetings,
        completed_meetings=completed_meetings,
        failed_meetings=failed_meetings,
        active_meetings=active_meetings,
        total_duration=total_duration,
        average_duration=average_duration
    )
    
    # Calculate usage patterns
    if meetings:
        # Most used platform
        platform_counts = {}
        for meeting in meetings:
            platform_counts[meeting.platform] = platform_counts.get(meeting.platform, 0) + 1
        most_used_platform = max(platform_counts, key=platform_counts.get) if platform_counts else None
        
        # Meetings per day (based on creation date)
        days_since_first = (datetime.utcnow() - min(m.created_at for m in meetings)).days + 1
        meetings_per_day = total_meetings / days_since_first if days_since_first > 0 else 0
        
        # Peak usage hours
        hour_counts = {}
        for meeting in meetings:
            hour = meeting.created_at.hour
            hour_counts[hour] = hour_counts.get(hour, 0) + 1
        peak_usage_hours = sorted(hour_counts.keys(), key=lambda h: hour_counts[h], reverse=True)[:3]
        
        # Last activity
        last_activity = max(m.created_at for m in meetings)
    else:
        most_used_platform = None
        meetings_per_day = 0.0
        peak_usage_hours = []
        last_activity = None
    
    usage_patterns = UserUsagePatterns(
        most_used_platform=most_used_platform,
        meetings_per_day=meetings_per_day,
        peak_usage_hours=peak_usage_hours,
        last_activity=last_activity
    )
    
    return UserAnalyticsResponse(
        user=UserDetailResponse.model_validate(user),
        meeting_stats=meeting_stats,
        usage_patterns=usage_patterns,
        api_tokens=[TokenResponse.model_validate(t) for t in user.api_tokens] if include_tokens else None
    )

# App events
@app.on_event("startup")
async def startup_event():
    logger.info("Admin API starting up. Skipping automatic DB initialization.")
    # The 'migrate-or-init' Makefile target is now responsible for all DB setup.
    # await init_db()
    pass

# Include the admin router
app.include_router(admin_router)
app.include_router(user_router)

# Root endpoint (optional)
@app.get("/")
async def root():
    return {"message": "Vexa Admin API"}
