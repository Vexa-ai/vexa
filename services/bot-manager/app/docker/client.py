import docker
import logging
import os
from typing import Dict, Any, Optional
from fastapi import HTTPException
from app.database.service import TranscriptionService

logger = logging.getLogger(__name__)

class DockerClient:
    """Client for Docker operations in local development environment"""
    
    def __init__(self):
        """Initialize Docker client"""
        self.client = docker.from_env()
        
        # Bot container configuration
        self.bot_image = os.getenv("BOT_IMAGE", "bot:latest")
        self.transcription_service = os.getenv("TRANSCRIPTION_SERVICE", "http://transcription-service:8080")
        self.network_name = os.getenv("DOCKER_NETWORK", "vexa_default")
    
    def _count_running_bots_for_user(self, user_id: str) -> int:
        """Counts the number of running bot containers for a specific user using labels."""
        try:
            containers = self.client.containers.list(
                filters={"label": f"vexa.user_id={user_id}", "status": "running"}
            )
            count = len(containers)
            logger.debug(f"Found {count} running bot containers for user {user_id}")
            return count
        except Exception as e:
            logger.error(f"Error counting running containers for user {user_id}: {e}")
            # Decide on behavior: raise the error or return 0/error indicator?
            # Returning 0 might be risky if it allows exceeding the limit due to a Docker error.
            # Let's re-raise for now, forcing the request to fail if Docker is inaccessible.
            raise
    
    def create_bot_container(self, user_id: str, meeting_id: str, meeting_url: Optional[str] = None) -> Dict[str, Any]:
        """Create a new bot container for specific user and meeting"""
        container_name = f"bot-{user_id}-{meeting_id}"
        
        # Check if container already exists
        try:
            existing_container = self.client.containers.get(container_name)
            if existing_container:
                logger.info(f"Container {container_name} already exists with status: {existing_container.status}")
                
                # Start container if it's not running
                if existing_container.status != "running":
                    existing_container.start()
                    logger.info(f"Started existing container {container_name}")
                
                return {"status": "exists", "container_name": container_name}
        except docker.errors.NotFound:
            # Container doesn't exist, continue to create it after checking limits
            pass
        except Exception as e:
            logger.error(f"Error checking container existence: {e}")
            raise
        
        # --- START: Bot Limit Check ---
        try:
            # Fetch user details (including max_concurrent_bots)
            # Convert user_id to int if necessary for DB query, depends on get_or_create_user expectation
            # Assuming user_id is passed as string and DB service handles conversion if needed
            user = TranscriptionService.get_or_create_user(user_id) # Ensure this doesn't error if user MUST exist
            if not user:
                 # This case might depend on how get_or_create_user handles failures
                 logger.error(f"User with ID {user_id} not found and could not be created.")
                 raise HTTPException(status_code=404, detail=f"User {user_id} not found.")

            # Count currently running bots for this user
            current_bot_count = self._count_running_bots_for_user(user_id)

            # Check against the user's limit
            # Ensure user.max_concurrent_bots is accessed correctly (it should be an attribute)
            user_limit = user.max_concurrent_bots # Store in variable for logging/checking
            logger.info(f"Checking bot limit for user {user_id}: Found {current_bot_count} running bots, limit is {user_limit}") # Added logging
            
            if not hasattr(user, 'max_concurrent_bots') or user_limit is None: # Check variable
                 logger.error(f"User {user_id} is missing the max_concurrent_bots attribute or it's None.")
                 # Default to a safe limit (e.g., 1) or deny if the attribute should always exist
                 raise HTTPException(status_code=500, detail="User configuration error: Bot limit not set.")

            if current_bot_count >= user_limit: # Check variable
                logger.warning(f"User {user_id} reached bot limit ({user_limit}). Cannot create new bot.") # Use variable in log
                raise HTTPException(
                    status_code=403, # Forbidden
                    detail=f"User has reached the maximum concurrent bot limit ({user_limit})." # Use variable in detail
                )
            logger.info(f"User {user_id} is under bot limit ({current_bot_count}/{user_limit}). Proceeding...") # Use variable in log

        except HTTPException as http_exc:
             raise http_exc # Re-raise HTTP exceptions directly
        except Exception as e:
             # Catch potential DB or Docker errors during the check
             logger.error(f"Error during bot limit check for user {user_id}: {e}")
             # Return a generic server error
             raise HTTPException(status_code=500, detail="Failed to verify bot limit.")
        # --- END: Bot Limit Check ---
        
        # Set default meeting URL if not provided
        if not meeting_url:
            meeting_url = "https://meet.google.com/xxx-xxxx-xxx"
            
        logger.info(f"Creating bot container for meeting URL: {meeting_url} for user {user_id}")
        
        # Create container
        try:
            container = self.client.containers.run(
                image=self.bot_image,
                name=container_name,
                detach=True,
                network=self.network_name,
                environment={
                    "USER_ID": user_id,
                    "MEETING_ID": meeting_id,
                    "MEETING_URL": meeting_url,
                    "TRANSCRIPTION_SERVICE": self.transcription_service
                },
                labels={"vexa.user_id": str(user_id)},
                restart_policy={"Name": "on-failure", "MaximumRetryCount": 3}
            )
            
            logger.info(f"Created container {container_name} with label vexa.user_id={user_id}")
            return {"status": "created", "container_name": container_name}
        except Exception as e:
            logger.error(f"Error creating container: {e}")
            raise
    
    def delete_bot_container(self, user_id: str, meeting_id: Optional[str] = None) -> Dict[str, Any]:
        """Delete a bot container by user_id and optionally meeting_id"""
        try:
            if meeting_id:
                container_name = f"bot-{user_id}-{meeting_id}"
                container = self.client.containers.get(container_name)
                container.stop()
                container.remove()
                logger.info(f"Deleted container {container_name}")
                return {"status": "deleted", "container_name": container_name}
            else:
                # Delete all containers for user
                containers = self.client.containers.list(
                    all=True, 
                    filters={"name": f"bot-{user_id}"}
                )
                for container in containers:
                    container.stop()
                    container.remove()
                    logger.info(f"Deleted container {container.name}")
                return {"status": "deleted", "count": len(containers)}
        except docker.errors.NotFound:
            logger.warning(f"Container not found for user {user_id}")
            return {"status": "not_found"}
        except Exception as e:
            logger.error(f"Error deleting container: {e}")
            raise
    
    def get_bot_status(self, user_id: str) -> list:
        """Get status of all bot containers for a user"""
        try:
            containers = self.client.containers.list(
                all=True, 
                filters={"name": f"bot-{user_id}"}
            )
            
            result = []
            for container in containers:
                # Extract meeting_id from container name (format: bot-{user_id}-{meeting_id})
                name_parts = container.name.split('-')
                meeting_id = name_parts[2] if len(name_parts) > 2 else "unknown"
                
                result.append({
                    "container_name": container.name,
                    "user_id": user_id,
                    "meeting_id": meeting_id,
                    "status": container.status,
                    "creation_time": container.attrs['Created'] if 'Created' in container.attrs else None
                })
            
            return result
        except Exception as e:
            logger.error(f"Error getting container status: {e}")
            raise 