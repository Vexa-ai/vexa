"""Subject-authenticated operations; MCP assembles this same OpenAPI surface."""
import json
import os
from pathlib import Path
from typing import Literal
import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import create_engine
from .store import CRMError, Store

class Body(BaseModel):
    model_config = ConfigDict(extra='forbid')
class Describe(Body):
    object_type: str = Field(default='', description='Optional object type; omit to discover permitted object types.')
class Search(Body):
    object_type: str
    filters: dict = Field(default_factory=dict, description='Equality filters on readable fields; discover field names with crm_describe.')
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)
class Resolve(Body):
    name: str = Field(min_length=1, max_length=300)
class Read(Body):
    record_id: str
class Change(Body):
    action: Literal['create','update','propose']
    idempotency_key: str = Field(default='', max_length=200, description='Required for create: stable caller-generated key reused when retrying the same request.')
    object_type: str = ''
    record_id: str = ''
    expected_revision: int = Field(default=0, ge=0)
    fields: dict = Field(default_factory=dict)
    narrative: str | None = None
    reason: str = Field(min_length=1)
    evidence: list[dict] = Field(default_factory=list)
class Configure(Body):
    object_type: str
    layout: dict = Field(default_factory=dict, description="Card layout: {title_field: 'Name', sections: [{title: 'Overview', fields: [{field: 'Amount', label: 'Investment', format: 'currency', currency: 'USD'}]}], show_narrative: true, show_related: true}. Field formats: text, markdown, number, currency, percent (0..100), date, badge.")
    expected_version: int = Field(default=0, ge=0)
    reason: str = Field(default='', max_length=2000)
class Review(Body):
    proposal_id: str
    accept: bool

class Identity:
    def __init__(self, base, secret, transport=None):
        self.base, self.secret, self.transport = base.rstrip('/'), secret, transport
    def __call__(self, request: Request):
        token=request.headers.get('x-api-key','')
        if not token:
            scheme, _, value=request.headers.get('authorization','').partition(' ')
            token=value if scheme.lower()=='bearer' else ''
        if not token:
            raise HTTPException(401,'Caller credential required')
        try:
            with httpx.Client(transport=self.transport, timeout=5) as c:
                response=c.post(self.base+'/internal/validate',headers={'X-Internal-Secret':self.secret},json={'token':token})
            if response.status_code==401:
                raise HTTPException(401,'Invalid caller credential')
            if response.status_code!=200:
                raise HTTPException(503,'Identity service unavailable')
            user_id=response.json().get('user_id')
            if user_id is None or str(user_id)=='':
                raise HTTPException(503,'Identity service unavailable')
            return str(user_id)
        except (httpx.HTTPError, ValueError) as e:
            raise HTTPException(503,'Identity service unavailable') from e

def create_app(store: Store, identity, tenant_id: str):
    if not tenant_id or not tenant_id.strip():
        raise ValueError("CRM_TENANT_ID is required")
    app=FastAPI(title='Vexa CRM',version='0.13.1')
    @app.middleware('http')
    async def check_binding(request, call_next):
        binding=request.headers.get('x-crm-tenant')
        if binding is not None and binding != tenant_id:
            return JSONResponse(status_code=403,content={'detail':'CRM instance binding mismatch'})
        return await call_next(request)

    @app.exception_handler(CRMError)
    async def error(request, exc):
        return JSONResponse(status_code=exc.status, content={'detail':str(exc)})
    @app.get('/health')
    def health():
        return {'status':'ok','domain':'crm'}
    @app.get('/.well-known/mcp-tools.json')
    def manifest():
        return json.loads((Path(__file__).resolve().parents[2]/'mcp.tools.v1.json').read_text())
    @app.post('/describe',operation_id='crm_describe')
    def describe(body: Describe, actor=Depends(identity)):
        """Discover CRM object types and readable fields in this instance. The tenant is configured by the server; never ask the user to choose one. Use before filtering or writing unfamiliar fields."""
        return store.describe(tenant_id,actor,body.object_type or None)
    @app.post('/search',operation_id='crm_search')
    def search(body: Search, actor=Depends(identity)):
        """Find authorized CRM records with equality filters and pagination. Results include stable IDs, revisions and href. Use each record href for entity links instead of workspace wikilinks. Present the top-level href as a Markdown link to open a live CRM table in the Minutes sidebar; clicking a record opens its card. Table columns follow the shared crm_configure layout. Restricted fields cannot be used as filters."""
        return store.search(tenant_id,actor,body.object_type,body.filters,body.limit,body.offset)
    @app.post('/resolve')
    def resolve_name(body: Resolve, actor=Depends(identity)):
        return store.resolve_name(tenant_id,actor,body.name)
    @app.post('/read',operation_id='crm_read')
    def read(body: Read, actor=Depends(identity)):
        """Read permitted fields, source evidence, links, pending proposals and current revision. Top-level id is the native UUID for subsequent calls; fields.Id is the imported source ID. Use href for the native Minutes record link. Read before changing an existing record."""
        return store.get(tenant_id,actor,body.record_id)
    @app.post('/change',operation_id='crm_change')
    def change(body: Change, actor=Depends(identity)):
        """Create a record, update readable/writable fields, or submit a proposal. Updates require the revision returned by crm_read. Include a reason and source evidence; proposal does not mean saved. Permission and review policy are server-enforced."""
        if body.action=='create':
            if not body.idempotency_key:raise HTTPException(400,'Create requires an idempotency_key')
            return store.create(tenant_id,actor,body.object_type,body.fields,body.narrative or '',body.reason,body.evidence,body.idempotency_key)
        return store.change(tenant_id,actor,body.record_id,body.expected_revision,body.fields,body.narrative,body.reason,body.evidence,body.action=='propose')
    @app.post('/history',operation_id='crm_history')
    def history(body: Read, actor=Depends(identity)):
        """Read up to 100 recent record revisions. Current access permissions filter historical values too."""
        return store.history(tenant_id,actor,body.record_id)
    @app.post('/review',operation_id='crm_review')
    def review(body: Review, actor=Depends(identity)):
        """Accept or reject a pending proposal when the caller has review and record-write rights. Acceptance conflicts if the record changed since proposal creation."""
        return store.review(tenant_id,actor,body.proposal_id,body.accept)
    @app.post('/configure',operation_id='crm_configure')
    def configure(body: Configure, actor=Depends(identity)):
        """Read or configure a shared per-object card layout (CRM administrators only). Omit layout to read its version; send a layout with expected_version and reason to save. Sections reference real fields; formats are text, markdown, number, currency, percent, date or badge. This changes presentation, never record values or access."""
        return store.configure(tenant_id,actor,body.object_type,body.layout or None,body.expected_version,body.reason)
    return app

def configured_app():
    url=os.environ.get('CRM_DATABASE_URL','')
    base=os.environ.get('ADMIN_API_URL','')
    secret=os.environ.get('INTERNAL_API_SECRET','')
    tenant_id=os.environ.get('CRM_TENANT_ID','').strip()
    if not all((url,base,secret,tenant_id)):
        raise RuntimeError('CRM_DATABASE_URL, ADMIN_API_URL, INTERNAL_API_SECRET and CRM_TENANT_ID are required for CRM service')
    if not url.startswith('postgresql+psycopg://'):
        raise RuntimeError('CRM deployment requires PostgreSQL')
    return create_app(Store(create_engine(url,pool_pre_ping=True,pool_size=2,max_overflow=3)),Identity(base,secret),tenant_id)
