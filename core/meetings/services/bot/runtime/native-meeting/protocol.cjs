// sdk-join.v1: mirrored by the published JSON schema and golden fixtures.
const states = ['initializing','authenticating','connecting','waiting_for_host','waiting_room','in_meeting','reconnecting','disconnecting','ended'];
const failures = ['invalid_config','runtime_missing','native_error','authentication_failed','join_failed','protocol_error','process_exit','timeout','cancelled','left','ended_before_admission'];
const exact = (v, keys) => v && typeof v === 'object' && !Array.isArray(v) && Object.keys(v).every(k => keys.includes(k));
function validConfig(v) {
  return exact(v,['meetingId','displayName','jwt','password','onBehalfToken','zak']) &&
    typeof v.meetingId === 'string' && /^\d{9,11}$/.test(v.meetingId) &&
    typeof v.displayName === 'string' && v.displayName.length > 0 && v.displayName.length <= 128 &&
    typeof v.jwt === 'string' && v.jwt.length > 0 && v.jwt.length <= 16384 &&
    ['password','onBehalfToken','zak'].every(k => v[k] === undefined || (typeof v[k] === 'string' && v[k].length <= 16384));
}
function validEvent(v) {
  if (!v || v.version !== 1) return false;
  if (v.kind === 'state') return exact(v,['version','kind','state']) && states.includes(v.state);
  return v.kind === 'failure' && exact(v,['version','kind','code','nativeCode']) && failures.includes(v.code) && (v.nativeCode === undefined || Number.isInteger(v.nativeCode));
}
module.exports = {states,failures,validConfig,validEvent,exact};
