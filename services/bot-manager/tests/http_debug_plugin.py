"""
Pytest plugin for debugging HTTP requests/responses.
Patches httpx to log all request/response bodies when HTTP_DEBUG=1
"""
import os
import json
from typing import Any
import httpx


def pytest_configure(config):
    """Configure HTTP debugging if HTTP_DEBUG=1"""
    if os.getenv('HTTP_DEBUG') != '1':
        return
    
    # Store original methods
    original_send = httpx.Client.send
    original_async_send = httpx.AsyncClient.send
    
    def debug_send(self, request: httpx.Request, **kwargs) -> httpx.Response:
        """Sync send wrapper with logging"""
        print_request(request)
        response = original_send(self, request, **kwargs)
        print_response(response)
        return response
    
    async def debug_async_send(self, request: httpx.Request, **kwargs) -> httpx.Response:
        """Async send wrapper with logging"""
        print_request(request)
        response = await original_async_send(self, request, **kwargs)
        print_response(response)
        return response
    
    # Patch httpx methods
    httpx.Client.send = debug_send
    httpx.AsyncClient.send = debug_async_send
    
    print("🔍 HTTP debugging enabled - all requests/responses will be logged")


def print_request(request: httpx.Request):
    """Print HTTP request details"""
    print(f"\n🔍 ➡️  HTTP REQUEST: {request.method} {request.url}")
    
    if request.headers:
        print("🔍 Request Headers:")
        for key, value in request.headers.items():
            print(f"🔍   {key}: {value}")
    
    if hasattr(request, 'content') and request.content:
        print("🔍 Request Body:")
        try:
            # Try to parse as JSON for pretty printing
            if request.headers.get('content-type', '').startswith('application/json'):
                body_text = request.content.decode('utf-8')
                parsed = json.loads(body_text)
                print(f"🔍   {json.dumps(parsed, indent=2)}")
            else:
                print(f"🔍   {request.content.decode('utf-8')}")
        except (UnicodeDecodeError, json.JSONDecodeError):
            print(f"🔍   <binary content: {len(request.content)} bytes>")
    print()


def print_response(response: httpx.Response):
    """Print HTTP response details"""
    print(f"🔍 ⬅️  HTTP RESPONSE: {response.status_code} {response.reason_phrase}")
    print(f"🔍   URL: {response.url}")
    
    if response.headers:
        print("🔍 Response Headers:")
        for key, value in response.headers.items():
            print(f"🔍   {key}: {value}")
    
    if response.content:
        print("🔍 Response Body:")
        try:
            # Try to parse as JSON for pretty printing
            if response.headers.get('content-type', '').startswith('application/json'):
                parsed = response.json()
                print(f"🔍   {json.dumps(parsed, indent=2)}")
            else:
                print(f"🔍   {response.text}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            print(f"🔍   <binary content: {len(response.content)} bytes>")
    print("-" * 60)