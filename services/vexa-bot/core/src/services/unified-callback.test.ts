import { callStatusChangeCallback } from './unified-callback';

let passed = 0;
let failed = 0;

function expect(name: string, actual: unknown, expected: unknown) {
  if (actual === expected) {
    console.log(`PASS ${name}`);
    passed++;
  } else {
    console.log(`FAIL ${name}`);
    console.log(`  expected: ${JSON.stringify(expected)}`);
    console.log(`  actual:   ${JSON.stringify(actual)}`);
    failed++;
  }
}

async function testInternalSecretHeader() {
  const calls: any[] = [];
  const originalFetch = globalThis.fetch;
  const originalSecret = process.env.INTERNAL_API_SECRET;

  (globalThis as any).fetch = async (url: string, init: any) => {
    calls.push({ url, init });
    return {
      ok: true,
      json: async () => ({ status: 'processed' }),
    } as Response;
  };

  try {
    process.env.INTERNAL_API_SECRET = 'env-secret';
    await callStatusChangeCallback(
      {
        meetingApiCallbackUrl: 'http://meeting-api:8080/bots/internal/callback/exited',
        connectionId: 'conn-123',
        container_name: 'meeting-42',
        internalSecret: 'bot-config-secret',
      },
      'completed',
      'self_initiated_leave',
      0,
    );

    expect('status_change endpoint', calls[0].url, 'http://meeting-api:8080/bots/internal/callback/status_change');
    expect('content type header', calls[0].init.headers['Content-Type'], 'application/json');
    expect('internal secret prefers bot config', calls[0].init.headers['X-Internal-Secret'], 'bot-config-secret');
  } finally {
    globalThis.fetch = originalFetch;
    if (originalSecret === undefined) {
      delete process.env.INTERNAL_API_SECRET;
    } else {
      process.env.INTERNAL_API_SECRET = originalSecret;
    }
  }
}

async function main() {
  await testInternalSecretHeader();
  console.log(`\n${passed} passed, ${failed} failed`);
  if (failed > 0) {
    process.exit(1);
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
