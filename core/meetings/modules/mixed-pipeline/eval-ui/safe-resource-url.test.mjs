import assert from 'node:assert/strict';
import { safeResourceUrl } from './safe-resource-url.mjs';

assert.equal(safeResourceUrl('/audio.wav'), 'http://127.0.0.1/audio.wav');
assert.equal(safeResourceUrl('http://127.0.0.1:8771/events'), 'http://127.0.0.1:8771/events');
assert.equal(safeResourceUrl('blob:http://127.0.0.1/example', { allowBlob: true }), 'blob:http://127.0.0.1/example');
assert.throws(() => safeResourceUrl('javascript:alert(1)'), /not allowed/);
assert.throws(() => safeResourceUrl('data:text/html,<script>alert(1)<\/script>'), /not allowed/);
assert.throws(() => safeResourceUrl('http://user:secret@127.0.0.1/audio.wav'), /not allowed/);

console.log('safe-resource-url: executable schemes and URL credentials are rejected');
