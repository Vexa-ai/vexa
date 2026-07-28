/**
 * ALLOY: Focused lifecycle gate for the production Whisper client slot.
 * Run: pnpm --filter @vexa/transcribe-whisper exec tsx src/concurrency-observer.test.ts
 */
import {
  TranscriptionClient,
  TranscriptionError,
  type TranscriptionExecutionObserver,
} from './index.js';

let failed = 0;
const check = (name: string, condition: boolean, detail = '') => {
  console.log(`  ${condition ? '✅' : '❌'} ${name}${condition ? '' : '  — ' + detail}`);
  if (!condition) failed++;
};

const realFetch = globalThis.fetch;
const pcm = new Float32Array(1600).fill(0.05);

function successResponse(text = 'ok'): Response {
  return new Response(JSON.stringify({
    text,
    language: 'en',
    duration: 0.1,
    segments: [],
  }), { status: 200 });
}

function observerFor(events: string[], durations: number[] = []): TranscriptionExecutionObserver {
  return {
    waiting: () => events.push('waiting'),
    started: () => events.push('started'),
    finished: (durationMs) => {
      durations.push(durationMs);
      events.push('finished');
    },
  };
}

function throwingObserverFor(events: string[]): TranscriptionExecutionObserver {
  return {
    waiting: () => {
      events.push('waiting');
      throw new Error('waiting observer failed');
    },
    started: () => {
      events.push('started');
      throw new Error('started observer failed');
    },
    finished: () => {
      events.push('finished');
      throw new Error('finished observer failed');
    },
  };
}

async function faultOf(fn: () => Promise<unknown>): Promise<TranscriptionError | null> {
  try {
    await fn();
    return null;
  } catch (error) {
    return error instanceof TranscriptionError ? error : null;
  }
}

async function run() {
  try {
    // ALLOY: RED 1 — the observer reports the real FIFO slot lifecycle, not request intent.
    {
      let fetchCalls = 0;
      let releaseFirstFetch!: () => void;
      const firstFetchGate = new Promise<void>((resolve) => {
        releaseFirstFetch = resolve;
      });
      (globalThis as any).fetch = async () => {
        fetchCalls++;
        if (fetchCalls === 1) await firstFetchGate;
        return successResponse();
      };

      const firstEvents: string[] = [];
      const secondEvents: string[] = [];
      const firstDurations: number[] = [];
      const secondDurations: number[] = [];
      const client = new TranscriptionClient({
        serviceUrl: 'http://stt.test',
        maxConcurrentRequests: 1,
      });

      const first = client.transcribe(
        pcm,
        'en',
        undefined,
        observerFor(firstEvents, firstDurations),
      );
      const second = client.transcribe(
        pcm,
        'en',
        undefined,
        observerFor(secondEvents, secondDurations),
      );

      check(
        'first request starts but does not finish while its fetch is held',
        JSON.stringify(firstEvents) === JSON.stringify(['started']),
        JSON.stringify(firstEvents),
      );
      check(
        'second request waits without starting while the slot is occupied',
        JSON.stringify(secondEvents) === JSON.stringify(['waiting']),
        JSON.stringify(secondEvents),
      );
      check('only the slot holder reaches fetch', fetchCalls === 1, `calls=${fetchCalls}`);

      releaseFirstFetch();
      await Promise.all([first, second]);

      check(
        'first lifecycle is started then finished',
        JSON.stringify(firstEvents) === JSON.stringify(['started', 'finished']),
        JSON.stringify(firstEvents),
      );
      check(
        'second lifecycle is waiting then started then finished',
        JSON.stringify(secondEvents) === JSON.stringify(['waiting', 'started', 'finished']),
        JSON.stringify(secondEvents),
      );
      check(
        'each execution reports one non-negative slot-held duration',
        firstDurations.length === 1
          && secondDurations.length === 1
          && firstDurations[0] >= 0
          && secondDurations[0] >= 0,
        JSON.stringify({ firstDurations, secondDurations }),
      );
    }

    // ALLOY: RED 2 negative control — unrestricted clients never report queue waiting.
    {
      let fetchCalls = 0;
      (globalThis as any).fetch = async () => {
        fetchCalls++;
        return successResponse('unrestricted');
      };
      const events: string[] = [];
      const client = new TranscriptionClient({
        serviceUrl: 'http://stt.test',
        maxConcurrentRequests: 0,
      });

      const result = await client.transcribe(
        pcm,
        'en',
        undefined,
        throwingObserverFor(events),
      );

      check('throwing diagnostics do not alter a successful result', result.text === 'unrestricted');
      check(
        'unrestricted execution reports started and finished but never waiting',
        JSON.stringify(events) === JSON.stringify(['started', 'finished']),
        JSON.stringify(events),
      );
      check('unrestricted execution reaches fetch once', fetchCalls === 1, `calls=${fetchCalls}`);
    }

    // ALLOY: RED 2 failure path — every failure releases the FIFO slot despite callback faults.
    {
      let fetchCalls = 0;
      (globalThis as any).fetch = async () => {
        fetchCalls++;
        return fetchCalls === 1
          ? new Response('invalid audio', { status: 400 })
          : successResponse('after failure');
      };
      const firstEvents: string[] = [];
      const secondEvents: string[] = [];
      const client = new TranscriptionClient({
        serviceUrl: 'http://stt.test',
        maxRetries: 0,
        maxConcurrentRequests: 1,
      });

      const first = faultOf(() => client.transcribe(
        pcm,
        'en',
        undefined,
        throwingObserverFor(firstEvents),
      ));
      const second = client.transcribe(
        pcm,
        'en',
        undefined,
        throwingObserverFor(secondEvents),
      );
      const [firstFault, secondResult] = await Promise.all([first, second]);

      check('first request still surfaces its typed failure', firstFault?.kind === 'bad_request');
      check('queued request starts and succeeds after the failed holder releases', secondResult.text === 'after failure');
      check(
        'failed holder still reports started and finished',
        JSON.stringify(firstEvents) === JSON.stringify(['started', 'finished']),
        JSON.stringify(firstEvents),
      );
      check(
        'queued successor survives throwing waiting, started, and finished callbacks',
        JSON.stringify(secondEvents) === JSON.stringify(['waiting', 'started', 'finished']),
        JSON.stringify(secondEvents),
      );
      check('failure and successor each reach fetch once', fetchCalls === 2, `calls=${fetchCalls}`);
    }

    // ALLOY: Regression — releasing a slot hands its permit directly to the oldest waiter.
    {
      const fetchReleases: Array<() => void> = [];
      let activeFetches = 0;
      let peakFetches = 0;
      (globalThis as any).fetch = async () => {
        activeFetches++;
        peakFetches = Math.max(peakFetches, activeFetches);
        await new Promise<void>((resolve) => fetchReleases.push(resolve));
        activeFetches--;
        return successResponse('held request');
      };

      const lifecycle: string[] = [];
      const executionOrder: number[] = [];
      let third: Promise<unknown> | undefined;
      const observer = (request: number, onFinished?: () => void): TranscriptionExecutionObserver => ({
        waiting: () => lifecycle.push(`${request}:waiting`),
        started: () => {
          lifecycle.push(`${request}:started`);
          executionOrder.push(request);
        },
        finished: () => {
          lifecycle.push(`${request}:finished`);
          onFinished?.();
        },
      });
      const client = new TranscriptionClient({
        serviceUrl: 'http://stt.test',
        maxConcurrentRequests: 1,
      });

      const first = client.transcribe(
        pcm,
        'en',
        'request 1',
        observer(1, () => queueMicrotask(() => {
          third = client.transcribe(pcm, 'en', 'request 3', observer(3));
        })),
      );
      const second = client.transcribe(pcm, 'en', 'request 2', observer(2));

      check(
        'second request reports waiting while the first fetch owns the only slot',
        JSON.stringify(lifecycle) === JSON.stringify(['1:started', '2:waiting']),
        JSON.stringify(lifecycle),
      );

      fetchReleases[0]!();
      await first;

      let releasedFetches = 1;
      while (releasedFetches < 3) {
        fetchReleases[releasedFetches]!();
        releasedFetches++;
        await new Promise<void>((resolve) => setImmediate(resolve));
      }
      await Promise.all([second, third!]);

      check(
        'release starts requests in FIFO order despite a later queued microtask',
        JSON.stringify(executionOrder) === JSON.stringify([1, 2, 3]),
        JSON.stringify(executionOrder),
      );
      check('FIFO handoff never overlaps held fetch executions', peakFetches === 1, `peak=${peakFetches}`);
      check(
        'all three observers report balanced waiting, started, and finished lifecycles',
        JSON.stringify(lifecycle) === JSON.stringify([
          '1:started',
          '2:waiting',
          '1:finished',
          '3:waiting',
          '2:started',
          '2:finished',
          '3:started',
          '3:finished',
        ]),
        JSON.stringify(lifecycle),
      );
    }
  } finally {
    (globalThis as any).fetch = realFetch;
  }

  if (failed) {
    console.error(`\n❌ concurrency observer: ${failed} check(s) FAILED.`);
    process.exit(1);
  }
  console.log('\n✅ concurrency observer: slot waiting and execution lifecycle are truthful and fault-isolated.');
}

run().catch((error) => {
  (globalThis as any).fetch = realFetch;
  console.error(error);
  process.exit(1);
});
