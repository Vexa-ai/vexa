'use strict';
// `sharp`, absent on purpose (README.md). @huggingface/transformers imports it at module scope and only
// checks that it is truthy; the image functions it guards are never reached from the audio-only mixed
// lane. Reaching one anyway is a typed fault that names itself, never an undefined-is-not-a-function.

class ImageBackendAbsent extends Error {
  constructor(operation) {
    super(`${operation}: no image backend in this build. Vexa replaces sharp (and its LGPL libvips ` +
      'binary) with @vexa/no-image-backend because no Vexa path decodes or encodes an image; a caller ' +
      'that needs one is new image processing and has to bring its own backend deliberately.');
    this.name = 'ImageBackendAbsent';
    this.code = 'ERR_VEXA_NO_IMAGE_BACKEND';
    this.operation = operation;
  }
}

function sharp() {
  throw new ImageBackendAbsent('sharp()');
}

// The module-level functions callers reach for before (or instead of) sharp(): Next's image optimizer
// calls block/unblock first, and others query cache/concurrency/simd.
for (const name of ['block', 'unblock', 'cache', 'concurrency', 'counters', 'simd']) {
  sharp[name] = () => { throw new ImageBackendAbsent(`sharp.${name}()`); };
}

sharp.ImageBackendAbsent = ImageBackendAbsent;
module.exports = sharp;
