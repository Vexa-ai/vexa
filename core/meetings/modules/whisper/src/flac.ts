/**
 * A minimal FLAC encoder — 16-bit mono PCM → a lossless .flac body for the STT upload.
 *
 * Why: a WAV window is 32 KB per second of audio. A slow uplink (measured 17–100 KB/s from a bot
 * host to the remote STT, 5% loss) turned a few minutes of WAV into a multi-megabyte upload that the
 * proxy cut mid-body. FLAC carries the same samples bit for bit in roughly half the bytes for speech
 * (less for silence), and every decoder on the path reads it: libsndfile (the bundled unit and the
 * gateway's duration count) and ffmpeg (the hosted model).
 *
 * Shape: STREAMINFO, then one frame per 4096 samples. Each subframe is the smallest of VERBATIM and
 * the FIXED predictors of order 0–4 with a Rice-coded residual (one partition). No external
 * dependency, no native code.
 */

const BLOCK = 4096;

class BitWriter {
  private buf: Uint8Array;
  private len = 0;      // bytes fully written
  private acc = 0;      // pending bits (MSB-first), < 32 of them
  private nacc = 0;
  constructor(capacity: number) { this.buf = new Uint8Array(Math.max(64, capacity)); }

  private ensure(extra: number): void {
    if (this.len + extra <= this.buf.length) return;
    const next = new Uint8Array(Math.max(this.buf.length * 2, this.len + extra));
    next.set(this.buf.subarray(0, this.len));
    this.buf = next;
  }

  /** Write the low `n` bits of `value` (n ≤ 32), MSB first. */
  bits(value: number, n: number): void {
    if (n === 0) return;
    if (n > 24) { this.bits(Math.floor(value / 2 ** 16) & ((1 << (n - 16)) - 1), n - 16); this.bits(value & 0xffff, 16); return; }
    this.acc = ((this.acc << n) | (value & ((1 << n) - 1))) >>> 0;
    this.nacc += n;
    this.ensure(4);
    while (this.nacc >= 8) {
      this.nacc -= 8;
      this.buf[this.len++] = (this.acc >>> this.nacc) & 0xff;
    }
    this.acc &= (1 << this.nacc) - 1;
  }

  /** `q` zero bits then a one (unary). */
  unary(q: number): void {
    while (q >= 24) { this.bits(0, 24); q -= 24; }
    this.bits(1, q + 1);
  }

  /** Pad to a byte boundary with zeros. */
  align(): void { if (this.nacc) this.bits(0, 8 - this.nacc); }

  get byteLength(): number { return this.len; }
  bytes(): Uint8Array { return this.buf.subarray(0, this.len); }
}

function crc8(data: Uint8Array, from: number, to: number): number {
  let crc = 0;
  for (let i = from; i < to; i++) {
    crc ^= data[i];
    for (let b = 0; b < 8; b++) crc = crc & 0x80 ? ((crc << 1) ^ 0x07) & 0xff : (crc << 1) & 0xff;
  }
  return crc;
}

function crc16(data: Uint8Array, from: number, to: number): number {
  let crc = 0;
  for (let i = from; i < to; i++) {
    crc ^= data[i] << 8;
    for (let b = 0; b < 8; b++) crc = crc & 0x8000 ? ((crc << 1) ^ 0x8005) & 0xffff : (crc << 1) & 0xffff;
  }
  return crc;
}

/** FLAC's UTF-8-style coding of the frame number. */
function utf8Number(w: BitWriter, n: number): void {
  if (n < 0x80) { w.bits(n, 8); return; }
  const k = n < 0x800 ? 2 : n < 0x10000 ? 3 : n < 0x200000 ? 4 : n < 0x4000000 ? 5 : 6;
  const prefix = (0xff << (8 - k)) & 0xff;
  w.bits(prefix | Math.floor(n / 2 ** (6 * (k - 1))), 8);
  for (let j = k - 2; j >= 0; j--) w.bits(0x80 | (Math.floor(n / 2 ** (6 * j)) & 0x3f), 8);
}

const zigzag = (r: number): number => (r >= 0 ? r * 2 : -r * 2 - 1);

/** Residual of the FIXED predictor of `order` over `x` (warm-up samples excluded). */
function fixedResidual(x: Int32Array, order: number): Int32Array {
  const n = x.length, out = new Int32Array(Math.max(0, n - order));
  for (let i = order; i < n; i++) {
    let p = 0;
    switch (order) {
      case 0: p = 0; break;
      case 1: p = x[i - 1]; break;
      case 2: p = 2 * x[i - 1] - x[i - 2]; break;
      case 3: p = 3 * x[i - 1] - 3 * x[i - 2] + x[i - 3]; break;
      default: p = 4 * x[i - 1] - 6 * x[i - 2] + 4 * x[i - 3] - x[i - 4];
    }
    out[i - order] = x[i] - p;
  }
  return out;
}

/** Best Rice parameter for a residual and the bits it costs (one partition). */
function riceCost(res: Int32Array): { k: number; bits: number } {
  let sum = 0;
  for (let i = 0; i < res.length; i++) sum += zigzag(res[i]);
  const mean = res.length ? sum / res.length : 0;
  let k = mean > 1 ? Math.min(14, Math.max(0, Math.floor(Math.log2(mean)))) : 0;
  const cost = (kk: number) => { let b = 0; for (let i = 0; i < res.length; i++) b += (zigzag(res[i]) >>> kk) + 1 + kk; return b; };
  let best = cost(k);
  for (const kk of [k - 1, k + 1]) {
    if (kk < 0 || kk > 14) continue;
    const c = cost(kk);
    if (c < best) { best = c; k = kk; }
  }
  return { k, bits: best };
}

function writeSubframe(w: BitWriter, x: Int32Array): void {
  const verbatimBits = x.length * 16;
  let bestOrder = -1, bestBits = verbatimBits, bestK = 0, bestRes: Int32Array | null = null;
  for (let order = 0; order <= 4 && order < x.length; order++) {
    const res = fixedResidual(x, order);
    const { k, bits } = riceCost(res);
    const total = order * 16 + 2 + 4 + 4 + bits;
    if (total < bestBits) { bestBits = total; bestOrder = order; bestK = k; bestRes = res; }
  }
  if (bestOrder < 0 || !bestRes) {
    w.bits(0x02, 8);                                         // VERBATIM, no wasted bits
    for (let i = 0; i < x.length; i++) w.bits(x[i] & 0xffff, 16);
    return;
  }
  w.bits((0b001000 | bestOrder) << 1, 8);                    // FIXED, order, no wasted bits
  for (let i = 0; i < bestOrder; i++) w.bits(x[i] & 0xffff, 16);
  w.bits(0, 2);                                              // residual coding: Rice, 4-bit parameter
  w.bits(0, 4);                                              // partition order 0
  w.bits(bestK, 4);
  for (let i = 0; i < bestRes.length; i++) {
    const u = zigzag(bestRes[i]);
    w.unary(u >>> bestK);
    if (bestK) w.bits(u & ((1 << bestK) - 1), bestK);
  }
}

/** Encode mono float PCM in [-1, 1] as a 16-bit FLAC file. */
export function encodeFlac(samples: Float32Array, sampleRate = 16000): Uint8Array {
  const pcm = new Int32Array(samples.length);
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    pcm[i] = Math.round(s < 0 ? s * 0x8000 : s * 0x7fff);
  }
  const w = new BitWriter(64 + samples.length);
  // "fLaC" + STREAMINFO (last metadata block, 34 bytes).
  w.bits(0x664c6143, 32);
  w.bits(1, 1); w.bits(0, 7); w.bits(34, 24);
  const blocks = Math.max(1, Math.ceil(pcm.length / BLOCK));
  const lastLen = pcm.length - (blocks - 1) * BLOCK;
  const minBlock = blocks === 1 ? Math.max(16, lastLen) : BLOCK;  // FLAC: min block ≥ 16 (the last may be shorter)
  w.bits(Math.min(minBlock, BLOCK), 16);
  w.bits(BLOCK, 16);
  w.bits(0, 24); w.bits(0, 24);                              // min/max frame size: unknown
  w.bits(sampleRate, 20); w.bits(0, 3); w.bits(15, 5);       // rate, 1 channel, 16 bits
  w.bits(Math.floor(pcm.length / 2 ** 32), 4); w.bits(pcm.length >>> 0, 32);
  for (let i = 0; i < 16; i++) w.bits(0, 8);                 // MD5: not computed (allowed)

  for (let f = 0; f < blocks; f++) {
    const block = pcm.subarray(f * BLOCK, Math.min(pcm.length, (f + 1) * BLOCK));
    if (block.length === 0) break;
    const start = w.byteLength;
    w.bits(0b11111111111110, 14); w.bits(0, 1); w.bits(0, 1);   // sync, reserved, fixed blocksize
    const exact = block.length === BLOCK;
    w.bits(exact ? 0b1100 : 0b0111, 4);                         // 4096, or 16-bit (n-1) at the end
    w.bits(0, 4);                                               // sample rate from STREAMINFO
    w.bits(0, 4); w.bits(0b100, 3); w.bits(0, 1);               // mono, 16 bits, reserved
    utf8Number(w, f);
    if (!exact) w.bits(block.length - 1, 16);
    const hdr = w.bytes();
    w.bits(crc8(hdr, start, w.byteLength), 8);
    writeSubframe(w, block);
    w.align();
    const all = w.bytes();
    w.bits(crc16(all, start, w.byteLength), 16);
  }
  return w.bytes().slice();
}
