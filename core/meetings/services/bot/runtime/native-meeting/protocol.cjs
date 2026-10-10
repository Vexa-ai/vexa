// sdk-join.v1 and sdk-capture.v1, compiled from the sealed schemas (P8, ADR-0039). The runtime keeps no
// hand copy of either wire: every check below is the schema's own, so a reseal changes the runtime with it
// and the two cannot drift. The schemas are read by path, as src/config.ts reads invocation.v1; the bot
// image carries core/meetings/contracts beside the bot. test/protocol.test.mjs holds the result to the goldens.
const {readFileSync}=require('node:fs');
const {join}=require('node:path');
const Ajv2020=require('ajv/dist/2020');
const CONTRACTS=join(__dirname,'..','..','..','..','contracts');
const ajv=new Ajv2020({strict:true,strictTypes:true});
for(const name of ['sdk-join.v1','sdk-capture.v1'])ajv.addSchema(JSON.parse(readFileSync(join(CONTRACTS,name,`${name.slice(0,-3)}.schema.json`),'utf8')));
function compile(name,shape){
  const validate=ajv.getSchema(`https://vexa.ai/schemas/${name}#/$defs/${shape}`);
  if(!validate)throw new Error(`${name} has no $defs/${shape}`);
  return value=>validate(value)===true;
}
module.exports={compile,validConfig:compile('sdk-join.v1','Config'),validCommand:compile('sdk-join.v1','Command'),validEvent:compile('sdk-join.v1','Event')};
