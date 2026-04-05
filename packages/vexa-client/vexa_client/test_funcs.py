import sys
sys.path.append('../vexa_client')
from vexa import VexaClient, parse_url
import test

from IPython.display import clear_output,display
import pandas as pd
import time

import random

import os
TOKEN = os.getenv('ADMIN_API_TOKEN')
url = "http://localhost:18056"

def create_user_client(admin_client,user_api_key=None):
    if user_api_key is None:
        # Use the new method that automatically sets user_id
        new_user = admin_client.create_user_and_set_id(email=f"{random.randint(1, 1000000)}@example.com", 
                                                       name="test",
                                                       max_concurrent_bots=1)

        # Create token using self.user_id (no need to pass user_id)
        token_info = admin_client.create_token()
        user_api_key = token_info['token']
        
        # Create client and attach user info
        client = VexaClient(
            base_url=url,
            api_key=user_api_key
        )
 
        return client
    else:
        client = VexaClient(
            base_url=url,
            api_key=user_api_key
        )
        return client

def request_bot(client,platform, native_meeting_id, passcode=None, bot_name="Vexa", language='en', task = 'transcribe'):
    meeting_info = client.request_bot(
        platform=platform,
        native_meeting_id=native_meeting_id,
        bot_name=bot_name,
        language=language,
        task = task,
        passcode=passcode
    )
    return meeting_info

def get_transcript(client,platform, native_meeting_id):
    # Extract meeting ID from URL if needed
    if '/' in native_meeting_id:
        native_meeting_id = native_meeting_id.split("/")[-1]
    
    try:
        for _ in range(10):
            transcript = client.get_transcript(native_meeting_id=native_meeting_id,platform=platform)
            df = pd.DataFrame(transcript['segments'])
            clear_output()
            display(df.sort_values('absolute_start_time').tail(10))
            time.sleep(1)

    except Exception as e:
        print(f"Error getting transcript: {e}")

