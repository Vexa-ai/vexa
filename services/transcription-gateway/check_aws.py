#!/usr/bin/env python3
"""
Check that AWS credentials and Transcribe Streaming are usable.
Run from repo root with gateway env, or inside the container:
  docker compose run --rm transcription-gateway python check_aws.py
"""
import asyncio
import os
import sys
REGION = "us-east-1"
LANGUAGE_CODE = "en-US"
SAMPLE_RATE_HZ = 16000
MEDIA_ENCODING = "pcm"

def check_env():
    region = os.environ.get("AWS_REGION", "").strip()
    if not region:
        print("FAIL: AWS_REGION is not set.")
        return False
    print(f"OK: AWS_REGION={region}")
    key = os.environ.get("AWS_ACCESS_KEY_ID", "").strip()
    secret = os.environ.get("AWS_SECRET_ACCESS_KEY", "").strip()
    if not key or not secret:
        print("WARN: AWS_ACCESS_KEY_ID or AWS_SECRET_ACCESS_KEY not set (relying on IAM role or default chain).")
    else:
        print("OK: AWS credentials are set (from env).")
    return True


def check_credentials():
    try:
        import boto3
    except ImportError:
        print("FAIL: boto3 not installed. pip install boto3")
        return False
    region = os.environ.get("AWS_REGION", REGION)
    try:
        sts = boto3.client("sts", region_name=region)
        identity = sts.get_caller_identity()
        print(f"OK: AWS identity: Account={identity['Account']}, Arn={identity['Arn']}")
        return True
    except Exception as e:
        print(f"FAIL: AWS credentials not valid: {e}")
        return False


async def check_transcribe_streaming():
    try:
        from amazon_transcribe.client import TranscribeStreamingClient
    except ImportError:
        print("FAIL: amazon-transcribe not installed. pip install amazon-transcribe")
        return False
    region = os.environ.get("AWS_REGION", REGION)
    try:
        client = TranscribeStreamingClient(region=region)
        stream = await client.start_stream_transcription(
            language_code=LANGUAGE_CODE,
            media_sample_rate_hz=SAMPLE_RATE_HZ,
            media_encoding=MEDIA_ENCODING,
        )
        await stream.input_stream.end_stream()
        print("OK: Transcribe Streaming API reachable (stream start/end succeeded).")
        return True
    except Exception as e:
        print(f"FAIL: Transcribe Streaming error: {e}")
        return False


def main():
    print("=== AWS Transcribe check for transcription-gateway ===\n")
    ok_env = check_env()
    print()
    ok_creds = check_credentials()
    print()
    ok_stream = asyncio.run(check_transcribe_streaming())
    print()
    if ok_env and ok_creds and ok_stream:
        print("All checks passed. Gateway can use AWS Transcribe Streaming.")
        return 0
    print("Some checks failed. Fix the failures above and set AWS_REGION, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY (or IAM role) for the transcription-gateway.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
