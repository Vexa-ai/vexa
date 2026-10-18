import uvicorn
uvicorn.run('crm.api:configured_app', factory=True, host='0.0.0.0', port=8300)
