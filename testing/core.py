import os
import random
import time
import pandas as pd
from vexa_client import VexaClient
from dotenv import load_dotenv
from IPython.display import clear_output, display

# Load environment variables
load_dotenv()

def create_user_client(user_api_key=None, base_url="http://localhost:18056", max_concurrent_bots=1, admin_api_key=None):
    """Create a VexaClient instance for a user."""
    if user_api_key is None:
        admin_client = VexaClient(base_url=base_url, admin_key=admin_api_key)
        
        new_user = admin_client.create_user(
            email=f"{random.randint(1, 1000000)}@example.com", 
            name="test",
            max_concurrent_bots=max_concurrent_bots
        )
        
        token_info = admin_client.create_token(user_id=new_user['id'])
        user_api_key = token_info['token']
        
    return VexaClient(base_url=base_url, api_key=user_api_key)

def request_bot(client, platform, native_meeting_id, passcode=None, 
                bot_name="Vexa bot", language='en', task='transcribe'):
    """Request a bot for a meeting."""
    return client.request_bot(
        platform=platform,
        native_meeting_id=native_meeting_id,
        bot_name=bot_name,
        language=language,
        task=task,
        passcode=passcode
    )

def get_transcript(client, platform, native_meeting_id, tail=10, duration=10):
    """Get and display transcript segments."""
    native_meeting_id = native_meeting_id.split("/")[-1]
    try:
        for _ in range(duration):
            transcript = client.get_transcript(native_meeting_id=native_meeting_id, platform=platform)
            df = pd.DataFrame(transcript['segments'])
            clear_output()
            display(df.sort_values('absolute_start_time').tail(tail))
            time.sleep(1)

        return df
    except Exception as e:
        print(e)