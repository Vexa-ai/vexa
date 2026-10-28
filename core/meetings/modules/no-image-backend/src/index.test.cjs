'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const sharp = require('./index.cjs');

test('the default export is truthy, which is all transformers checks at import', () => {
  assert.equal(typeof sharp, 'function');
  assert.ok(sharp);
});

test('every image call is a typed fault naming the operation', () => {
  for (const [call, op] of [[() => sharp(Buffer.alloc(4)), 'sharp()'], [() => sharp.block({}), 'sharp.block()'],
    [() => sharp.unblock({}), 'sharp.unblock()'], [() => sharp.concurrency(1), 'sharp.concurrency()']]) {
    assert.throws(call, (e) => e instanceof sharp.ImageBackendAbsent && e.code === 'ERR_VEXA_NO_IMAGE_BACKEND'
      && e.operation === op && e.message.includes('no image backend'));
  }
});

test('an ESM default import sees the same function', async () => {
  const m = await import('./index.cjs');
  assert.equal(m.default, sharp);
});
