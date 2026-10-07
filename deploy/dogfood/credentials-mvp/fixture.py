"""Fixed fake provider: authenticates an MVP-only key; never logs/echoes it."""
import hmac
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None)
@app.post('/verify')
def verify(request:Request):
    expected=Path('/fixture/key').read_text().strip()
    actual=request.headers.get('authorization','').removeprefix('Bearer ')
    if not hmac.compare_digest(actual,expected):return JSONResponse({'error':'Authentication refused'},401)
    return {'receipt':'fixture-'+request.headers.get('x-operation-id','unknown')}
