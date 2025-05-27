import logging
import secrets
import string
import os
from fastapi import FastAPI, APIRouter, Depends, HTTPException, status, Security, Response
from fastapi.security import APIKeyHeader
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from typing import List # Import List for response model
from datetime import datetime # Import datetime
from sqlalchemy import func
from pydantic import BaseModel

# Import shared models and schemas
from shared_models.models import User, APIToken, Base, Meeting # Import Base for init_db and Meeting
from shared_models.schemas import UserCreate, UserResponse, TokenResponse, UserDetailResponse, UserBase, UserUpdate, MeetingResponse # Import UserBase for update and UserUpdate schema

# Database utilities (needs to be created)
from shared_models.database import get_db, init_db , recreate_db # New import

# Logging configuration
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("admin_api")

# App initialization
app = FastAPI(title="Vexa Admin API")

# --- Pydantic Schemas for new endpoint ---
class MeetingUserStat(MeetingResponse): # Inherit from MeetingResponse to get meeting fields
    user: UserResponse # Embed UserResponse

class PaginatedMeetingUserStatResponse(BaseModel):
    total: int
    items: List[MeetingUserStat]

# Security - Reuse logic from bot-manager/auth.py for admin token verification
API_KEY_HEADER = APIKeyHeader(name="X-Admin-API-Key", auto_error=False) # Use a distinct header
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

# Router setup (all routes require admin token verification)
router = APIRouter(
    prefix="/admin",
    tags=["Admin"],
    dependencies=[Depends(verify_admin_token)]
)

# --- Helper Functions --- 
def generate_secure_token(length=40):
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for i in range(length))

# --- Admin Endpoints (Copied and adapted from bot-manager/admin.py) --- 
@router.post("/users",
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
        response.status_code = status.HTTP_200_OK
        return UserResponse.from_orm(existing_user)

    user_data = user_in.dict()
    db_user = User(
        email=user_data['email'],
        name=user_data.get('name'),
        image_url=user_data.get('image_url')
    )
    db.add(db_user)
    await db.commit()
    await db.refresh(db_user)
    logger.info(f"Admin created user: {db_user.email} (ID: {db_user.id})")
    return UserResponse.from_orm(db_user)

@router.get("/users", 
            response_model=List[UserResponse], # Use List import
            summary="List all users")
async def list_users(skip: int = 0, limit: int = 100, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).offset(skip).limit(limit))
    users = result.scalars().all()
    return [UserResponse.from_orm(u) for u in users]

@router.get("/users/{user_id}", 
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
        
    # Return the user object. Pydantic will handle serialization using UserDetailResponse.
    return user

@router.patch("/users/{user_id}",
             response_model=UserResponse,
             summary="Update user details",
             description="Update user's name, image URL, or max concurrent bots.")
async def update_user(user_id: int, user_update: UserUpdate, db: AsyncSession = Depends(get_db)):
    """
    Updates specific fields of a user.
    Only provide the fields you want to change in the request body.
    Requires admin privileges.
    """
    # Fetch the user to update
    result = await db.execute(select(User).where(User.id == user_id))
    db_user = result.scalars().first()

    if not db_user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    # Get the update data, excluding unset fields to only update provided values
    update_data = user_update.dict(exclude_unset=True)

    # Prevent changing email via this endpoint (if desired)
    if 'email' in update_data and update_data['email'] != db_user.email:
         raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot change user email via this endpoint.")
    elif 'email' in update_data:
         del update_data['email'] # Don't attempt to update email to the same value

    # Update the user object attributes
    updated = False
    for key, value in update_data.items():
        if hasattr(db_user, key) and getattr(db_user, key) != value:
            setattr(db_user, key, value)
            updated = True

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

    return UserResponse.from_orm(db_user)

@router.post("/users/{user_id}/tokens", 
             response_model=TokenResponse,
             status_code=status.HTTP_201_CREATED,
             summary="Generate a new API token for a user")
async def create_token_for_user(user_id: int, db: AsyncSession = Depends(get_db)):
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    
    token_value = generate_secure_token()
    # Use the APIToken model from shared_models
    db_token = APIToken(token=token_value, user_id=user_id)
    db.add(db_token)
    await db.commit()
    await db.refresh(db_token)
    logger.info(f"Admin created token for user {user_id} ({user.email})")
    # Use TokenResponse for consistency with schema definition (datetime object)
    return TokenResponse.from_orm(db_token)

@router.delete("/tokens/{token_id}", 
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
@router.get("/stats/meetings-users",
            response_model=PaginatedMeetingUserStatResponse,
            summary="Get paginated list of meetings joined with users")
async def list_meetings_with_users(
    skip: int = 0, 
    limit: int = 100, 
    db: AsyncSession = Depends(get_db)
):
    """
    Retrieves a paginated list of all meetings, joined with their associated user data.
    """
    # Query to count total items
    count_query = select(func.count(Meeting.id))
    total_result = await db.execute(count_query)
    total = total_result.scalar_one_or_none() or 0

    # Query to fetch items with join and pagination
    query = (
        select(Meeting)
        .join(User, Meeting.user_id == User.id)
        .options(selectinload(Meeting.user)) # Eager load the user relationship
        .order_by(Meeting.created_at.desc()) # Example ordering
        .offset(skip)
        .limit(limit)
    )
    result = await db.execute(query)
    meetings = result.scalars().all()

    # Prepare the response items
    response_items = []
    for meeting in meetings:
        # The user should be loaded due to selectinload
        # We construct MeetingUserStat which expects a UserResponse for the 'user' field
        user_response = UserResponse.from_orm(meeting.user) if meeting.user else None
        meeting_user_stat = MeetingUserStat(
            **MeetingResponse.from_orm(meeting).dict(), # Populate Meeting fields
            user=user_response # Add the UserResponse
        )
        response_items.append(meeting_user_stat)
        
    return PaginatedMeetingUserStatResponse(total=total, items=response_items)

# TODO: Add endpoints for GET /users/{id}, DELETE /users/{id}

# Include the router in the main app
app.include_router(router)

# Add startup event to initialize DB (if needed for this service)
@app.on_event("startup")
async def startup_event():
    logger.info("Starting up Admin API...")
    # Requires database_utils.py to be created in admin-api/app
    await init_db() 
    #await recreate_db()
    logger.info("Database initialized.")

# Root endpoint (optional)
@app.get("/")
async def root():
    return {"message": "Vexa Admin API"}
