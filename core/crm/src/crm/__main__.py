"""``python -m crm`` — crm-api: the config.v1 preflight, then the service on port 8300."""
import logging

import uvicorn

from .config_preflight import preflight

logging.basicConfig(level=logging.INFO)
preflight()
uvicorn.run('crm.api:configured_app', factory=True, host='0.0.0.0', port=8300)
