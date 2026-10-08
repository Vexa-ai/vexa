import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync, readdirSync} from 'node:fs';
import Ajv2020 from 'ajv/dist/2020.js';
import protocol from '../capture-protocol.cjs';

const root=new URL('../../../../../contracts/sdk-capture.v1/',import.meta.url);
const schema=JSON.parse(readFileSync(new URL('sdk-capture.schema.json',root)));
const validate=new Ajv2020({strict:false}).compile({$defs:schema.$defs,$ref:'#/$defs/Event'});
test('native capture IPC matches sealed fixtures and rejects extra properties',()=>{
  const events=readdirSync(new URL('golden/',root)).filter(n=>n.startsWith('Event.')&&n.endsWith('.json')).map(n=>JSON.parse(readFileSync(new URL(`golden/${n}`,root))));
  events.push({version:1,kind:'capture-failure',code:'backpressure'});
  for(const event of events){
    assert.equal(validate(event),true);assert.equal(protocol.valid(event),true);
    const corrupt={...event,unexpected:'not in contract'};
    assert.equal(validate(corrupt),false);assert.equal(protocol.valid(corrupt),false);
  }
  for(const invalid of [null,[],{}, {version:1,kind:'capture-state',state:'ready'}, {version:1,kind:'capture-failure',code:'arbitrary'}]){
    assert.equal(validate(invalid),false);assert.equal(protocol.valid(invalid),false);
  }
});
