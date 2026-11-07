export function pcmToWavLegacy(samples: Float32Array, sampleRate = 16000): ArrayBuffer {
  const dataSize = samples.length * 2; const buf = new ArrayBuffer(44 + dataSize); const v = new DataView(buf);
  const a = (o: number, s: string) => { for (let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)); };
  a(0, "RIFF"); v.setUint32(4, 36 + dataSize, true); a(8, "WAVE"); a(12, "fmt "); v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
  v.setUint32(24, sampleRate, true); v.setUint32(28, sampleRate * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true); a(36, "data"); v.setUint32(40, dataSize, true);
  for (let i = 0; i < samples.length; i++) { const s = Math.max(-1, Math.min(1, samples[i])); v.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true); }
  return buf;
}
