/**
 * Tests for defaultBotName — reads DEFAULT_BOT_NAME env at call time.
 *
 * Run: npx tsx core/meetings/modules/join/src/__tests__/defaultBotName.test.ts
 */

import { defaultBotName } from '../index';

let passed = 0;
let failed = 0;

function assert(name: string, actual: any, expected: any) {
  if (actual === expected) {
    console.log(`  \x1b[32mPASS\x1b[0m  ${name}`);
    passed++;
  } else {
    console.log(`  \x1b[31mFAIL\x1b[0m  ${name}`);
    console.log(`        expected: ${JSON.stringify(expected)}`);
    console.log(`        actual:   ${JSON.stringify(actual)}`);
    failed++;
  }
}

function withCleanup(): () => void {
  const prev = process.env.DEFAULT_BOT_NAME;
  return () => {
    if (prev === undefined) delete process.env.DEFAULT_BOT_NAME;
    else process.env.DEFAULT_BOT_NAME = prev;
  };
}

// Test 1: fallback when unset
{
  const restore = withCleanup();
  delete process.env.DEFAULT_BOT_NAME;
  assert('fallback to "Vexa Join Layer" when env unset', defaultBotName(), 'Vexa Join Layer');
  restore();
}

// Test 2: reads env at call time (not frozen at import)
{
  const restore = withCleanup();
  delete process.env.DEFAULT_BOT_NAME;
  assert('initial call without env', defaultBotName(), 'Vexa Join Layer');
  process.env.DEFAULT_BOT_NAME = 'MyBot';
  assert('after setting env', defaultBotName(), 'MyBot');
  delete process.env.DEFAULT_BOT_NAME;
  assert('after deleting env', defaultBotName(), 'Vexa Join Layer');
  restore();
}

// Test 3: trims whitespace
{
  const restore = withCleanup();
  process.env.DEFAULT_BOT_NAME = '  Assistant  ';
  assert('trims surrounding whitespace', defaultBotName(), 'Assistant');
  restore();
}

console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) process.exit(1);
