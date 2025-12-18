#!/usr/bin/env python3
"""
Generate test audio file for transcription service testing.
This script creates a WAV file with speech that can be used for testing.
"""

import os
import sys
import subprocess
import shutil
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
TEST_AUDIO = SCRIPT_DIR / "test_audio.wav"
TEST_TEXT = "Hello, this is a test of the transcription service. Can you hear me clearly?"

def generate_audio():
    """Generate test audio file using gTTS and ffmpeg"""
    try:
        from gtts import gTTS
    except ImportError:
        print("Error: gTTS not installed. Install with: pip install gtts")
        sys.exit(1)
    
    if not shutil.which("ffmpeg"):
        print("Error: ffmpeg not found. Install with: apt-get install ffmpeg")
        sys.exit(1)
    
    print(f"Generating test audio: {TEST_AUDIO}")
    print(f"Text: {TEST_TEXT}")
    
    # Generate speech using gTTS
    print("1. Generating speech with gTTS...")
    tts = gTTS(TEST_TEXT, lang='en')
    temp_mp3 = SCRIPT_DIR / "temp_test.mp3"
    tts.save(str(temp_mp3))
    
    # Convert to WAV format (16kHz mono) using ffmpeg
    print("2. Converting to WAV format (16kHz mono)...")
    subprocess.run([
        'ffmpeg', '-i', str(temp_mp3),
        '-ar', '16000',  # Sample rate
        '-ac', '1',      # Mono
        str(TEST_AUDIO),
        '-y'             # Overwrite
    ], check=True, capture_output=True)
    
    # Cleanup
    temp_mp3.unlink()
    
    if TEST_AUDIO.exists() and TEST_AUDIO.stat().st_size > 0:
        size_kb = TEST_AUDIO.stat().st_size / 1024
        print(f"✓ Test audio generated successfully: {TEST_AUDIO} ({size_kb:.1f} KB)")
        return True
    else:
        print("✗ Failed to generate test audio file")
        return False

if __name__ == "__main__":
    generate_audio()









