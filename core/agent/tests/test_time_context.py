import importlib.util
from pathlib import Path
from datetime import datetime,timezone
spec=importlib.util.spec_from_file_location('time_context',Path(__file__).parents[1]/'mcp/time_context.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
def test_lisbon_dst_and_winter():
 for month,hour in ((10,21),(12,20)):
  r=m.clock_context('Europe/Lisbon',datetime(2026,month,7,20,20,tzinfo=timezone.utc))
  assert r['local_now'].startswith(f'2026-{month:02}-07T{hour}:20')
  assert not r['timezone_required']
def test_unknown_and_invalid_ask_without_guessing():
 for zone in ('','invalid/zone'):
  r=m.clock_context(zone)
  assert r['timezone_required'] and r['timezone'] is None and 'local_now' not in r


def test_registered_clock_reads_saved_preference_and_enriches_waiting():
 from types import SimpleNamespace
 import json
 class Registry:
  def __init__(self):self.tools={}
  def tool(self):
   def add(fn):self.tools[fn.__name__]=fn;return fn
   return add
  def remove_tool(self,name):self.tools.pop(name,None)
 registry=Registry();saved={};calls=[]
 def http(*args):calls.append(args);return 200,saved
 def store(actor,key,value):saved[key]=value;return saved,None
 runtime=SimpleNamespace(mcp=registry,_anon_guard=lambda fn:fn,_http=http,ADMIN_API='http://identity',me=lambda:'owner',_internal_headers=lambda:{},CALL_SCOPE=SimpleNamespace(get=lambda:{'regime':'human'}),_settings_set=store,whats_waiting=lambda:json.dumps({'waiting':[]}))
 m.register(runtime)
 assert json.loads(registry.tools['current_time']())['timezone_required']
 assert json.loads(registry.tools['timezone_set']('Europe/Lisbon'))['status']=='saved'
 result=json.loads(registry.tools['whats_waiting']())
 assert result['waiting']==[] and result['time_context']['timezone']=='Europe/Lisbon'
 assert len(calls)==2


def test_preference_outage_is_not_reported_as_missing_timezone():
 from types import SimpleNamespace
 import json
 class Registry:
  def __init__(self):self.tools={}
  def tool(self):
   def add(fn):self.tools[fn.__name__]=fn;return fn
   return add
  def remove_tool(self,name):pass
 registry=Registry()
 runtime=SimpleNamespace(mcp=registry,_anon_guard=lambda fn:fn,_http=lambda *args:(503,{}),ADMIN_API='http://identity',me=lambda:'owner',_internal_headers=lambda:{},whats_waiting=lambda:'{}')
 m.register(runtime)
 result=json.loads(registry.tools['current_time']())
 assert result['preference_status']=='unavailable'
 assert 'Do not claim they are missing' in result['instruction']
