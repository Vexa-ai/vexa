from celery import Celery
import os
import logging
from datetime import datetime, timedelta
from ..kubernetes.client import KubernetesClient

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configure Celery
redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")
celery_app = Celery("bot_monitoring", broker=redis_url, backend=redis_url)

# Configure Celery to use a specific time zone
celery_app.conf.timezone = "UTC"

# Configure the Kubernetes client
k8s_client = KubernetesClient()

@celery_app.task
def monitor_bot_containers():
    """
    Monitors all bot containers and logs their status.
    Can be extended to restart failed containers or clean up stale ones.
    """
    try:
        # Get all bot pods
        pod_list = k8s_client.core_v1.list_namespaced_pod(
            namespace=k8s_client.namespace,
            label_selector="app=bot"
        )
        
        logger.debug(f"Monitoring {len(pod_list.items)} bot containers")
        
        # Check pod statuses
        for pod in pod_list.items:
            pod_name = pod.metadata.name
            user_id = pod.metadata.labels.get("user-id", "unknown")
            meeting_id = pod.metadata.labels.get("meeting-id", "unknown")
            status = pod.status.phase
            
            creation_time = pod.metadata.creation_timestamp
            age = datetime.now(creation_time.tzinfo) - creation_time
            
            logger.debug(f"Bot: {pod_name}, Status: {status}, Age: {age}")
            
            # Check for failed pods and restart them
            if status == "Failed":
                logger.debug(f"Detected failed pod: {pod_name}. Restarting...")
                k8s_client.delete_bot_pod(user_id, meeting_id)
                k8s_client.create_bot_pod(user_id, meeting_id)
                logger.debug(f"Restarted failed pod: {pod_name}")
            
            # Clean up completed pods that are older than 1 hour
            if status == "Succeeded" and age > timedelta(hours=1):
                logger.debug(f"Cleaning up old succeeded pod: {pod_name}")
                k8s_client.delete_bot_pod(user_id, meeting_id)
        
        return {"status": "success", "pods_monitored": len(pod_list.items)}
    except Exception as e:
        logger.debug(f"Error during bot monitoring: {e}")
        return {"status": "error", "message": str(e)}

@celery_app.task
def clean_idle_bots(idle_threshold_minutes=30):
    """
    Clean up bot containers that have been idle for too long
    """
    try:
        # Get all running bot pods
        pod_list = k8s_client.core_v1.list_namespaced_pod(
            namespace=k8s_client.namespace,
            label_selector="app=bot"
        )
        
        cleaned_count = 0
        
        # Check pod statuses
        for pod in pod_list.items:
            pod_name = pod.metadata.name
            user_id = pod.metadata.labels.get("user-id", "unknown")
            meeting_id = pod.metadata.labels.get("meeting-id", "unknown")
            
            # Check the last activity timestamp (this would require custom logic in your bot application)
            # For example, your bot could update a ConfigMap with its last activity timestamp
            # Here we're just using the pod's creation time as an example
            creation_time = pod.metadata.creation_timestamp
            age = datetime.now(creation_time.tzinfo) - creation_time
            
            if age > timedelta(minutes=idle_threshold_minutes):
                logger.debug(f"Cleaning up idle pod: {pod_name}, Age: {age}")
                k8s_client.delete_bot_pod(user_id, meeting_id)
                cleaned_count += 1
        
        return {"status": "success", "pods_cleaned": cleaned_count}
    except Exception as e:
        logger.debug(f"Error during idle bot cleanup: {e}")
        return {"status": "error", "message": str(e)} 