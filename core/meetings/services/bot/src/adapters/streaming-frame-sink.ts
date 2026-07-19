import net from 'node:net';

const SAMPLE_RATE = 16000;
const FRAME_MS = 100;
const SAMPLES_PER_FRAME = (SAMPLE_RATE * FRAME_MS) / 1000; // 1600 samples @ 16 kHz
const BYTES_PER_FRAME = SAMPLES_PER_FRAME * 2; // 2 bytes per Int16 sample = 3200 bytes

export interface StreamingFrameSink {
  /** Feed one Float32 PCM frame (any length, 16 kHz mono). Multiple streams are mixed internally. */
  feedAudio(pcm: Float32Array): void;
  /** Close the socket and stop the timer. */
  stop(): Promise<void>;
}

/**
 * Stream captured audio from the Vexa bot to the perceive8 adapter over a local
 * TCP socket. Vexa's AudioWorklet emits 4096-sample Float32 frames (256 ms @ 16 kHz)
 * per captured speaker stream; this sink mixes those streams, converts Float32 → Int16,
 * and rechunks them into the 100 ms / 3200-byte frames the adapter's stream-ms pacer
 * expects.
 *
 * The URL must be `tcp://host:port` (e.g. `tcp://127.0.0.1:8081`).
 */
export function createStreamingFrameSink(frameUrl: string): StreamingFrameSink {
  const match = frameUrl.match(/^tcp:\/\/([^:]+):(\d+)$/);
  if (!match) throw new Error(`invalid frame url: ${frameUrl}`);
  const [, host, port] = match;

  // Time-aligned mixer: incoming frames from any channel are placed at their
  // capture timestamp so overlapping speakers are mixed correctly.
  let mixed = new Float32Array(0);
  let startTime = 0;
  let initialized = false;

  const queue: Buffer[] = [];
  let connected = false;
  let socket: net.Socket | null = null;

  const send = (buf: Buffer) => {
    if (connected && socket) {
      socket.write(buf);
    } else {
      queue.push(buf);
    }
  };

  socket = net.createConnection({ host, port: Number(port) }, () => {
    connected = true;
    while (queue.length) {
      socket!.write(queue.shift()!);
    }
  });

  socket.on('error', (err) => {
    console.error(`[streaming-frame-sink] socket error: ${err.message}`);
  });

  socket.on('close', () => {
    connected = false;
  });

  const timer = setInterval(() => {
    if (mixed.length < SAMPLES_PER_FRAME) return;
    const frame = mixed.subarray(0, SAMPLES_PER_FRAME);
    const int16 = new Int16Array(SAMPLES_PER_FRAME);
    for (let i = 0; i < SAMPLES_PER_FRAME; i++) {
      const s = frame[i] ?? 0;
      int16[i] = Math.round(Math.max(-1, Math.min(1, s)) * 32767);
    }
    send(Buffer.from(int16.buffer));
    mixed = mixed.subarray(SAMPLES_PER_FRAME);
    startTime += FRAME_MS;
  }, FRAME_MS);

  return {
    feedAudio(pcm) {
      const ts = Date.now();
      if (!initialized) {
        startTime = ts;
        initialized = true;
      }
      const startSample = Math.max(0, Math.round((ts - startTime) / 1000 * SAMPLE_RATE));
      const endSample = startSample + pcm.length;
      if (endSample > mixed.length) {
        const next = new Float32Array(endSample);
        next.set(mixed);
        mixed = next;
      }
      for (let i = 0; i < pcm.length; i++) {
        mixed[startSample + i] += pcm[i];
      }
    },
    stop() {
      clearInterval(timer);
      return new Promise<void>((resolve) => {
        if (!socket) return resolve();
        socket.once('close', resolve);
        socket.end();
        setTimeout(resolve, 1000);
      });
    },
  };
}
