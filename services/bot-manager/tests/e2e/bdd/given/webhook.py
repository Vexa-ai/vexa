"""
GIVEN helpers for webhook setup and mocking.
"""
import json
import os


def webhooks_are_mocked_for_meeting(meeting, respx_module, httpx_module):
    """GIVEN webhooks are mocked for the meeting if configured."""
    webhook_request = None
    webhook_url = None
    
    if meeting.user.data and meeting.user.data.get('webhook_url'):
        webhook_url = meeting.user.data['webhook_url']
        
        # Use flexible URL matching for complex webhook URLs with query parameters
        from urllib.parse import urlparse
        parsed_url = urlparse(webhook_url)
        base_url = f"{parsed_url.scheme}://{parsed_url.netloc}{parsed_url.path}"
        
        webhook_request = respx_module.post(url__startswith=base_url).mock(
            return_value=httpx_module.Response(200, json={"status": "received"})
        )
        
    return webhook_request, webhook_url