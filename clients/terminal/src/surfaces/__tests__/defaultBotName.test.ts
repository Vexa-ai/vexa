import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { defaultBotName, loadDefaultBotName, __resetDefaultBotNameCache } from '../defaultBotName';

describe('defaultBotName', () => {
  beforeEach(() => {
    __resetDefaultBotNameCache();
    delete process.env.NEXT_PUBLIC_DEFAULT_BOT_NAME;
  });

  afterEach(() => {
    __resetDefaultBotNameCache();
    delete process.env.NEXT_PUBLIC_DEFAULT_BOT_NAME;
  });

  it('returns "Vexa" when nothing is set', () => {
    expect(defaultBotName()).toBe('Vexa');
  });

  it('falls back to NEXT_PUBLIC_DEFAULT_BOT_NAME (build-time) at call time', () => {
    expect(defaultBotName()).toBe('Vexa');
    process.env.NEXT_PUBLIC_DEFAULT_BOT_NAME = 'MyBot';
    expect(defaultBotName()).toBe('MyBot');
    delete process.env.NEXT_PUBLIC_DEFAULT_BOT_NAME;
    expect(defaultBotName()).toBe('Vexa');
  });

  it('trims whitespace on the build-time fallback', () => {
    process.env.NEXT_PUBLIC_DEFAULT_BOT_NAME = '  Assistant  ';
    expect(defaultBotName()).toBe('Assistant');
  });

  it('prefers the runtime DEFAULT_BOT_NAME (via /api/config) over the build-time fallback', async () => {
    process.env.NEXT_PUBLIC_DEFAULT_BOT_NAME = 'BuildTimeBot';
    const orig = globalThis.fetch;
    globalThis.fetch = (async () => ({
      ok: true,
      json: async () => ({ defaultBotName: '  Runtime Bot  ' }),
    })) as unknown as typeof fetch;
    try {
      await loadDefaultBotName();
      expect(defaultBotName()).toBe('Runtime Bot'); // runtime wins + trimmed
    } finally {
      globalThis.fetch = orig;
    }
  });

  it('keeps the fallback when /api/config has no name', async () => {
    const orig = globalThis.fetch;
    globalThis.fetch = (async () => ({ ok: true, json: async () => ({ defaultBotName: null }) })) as unknown as typeof fetch;
    try {
      await loadDefaultBotName();
      expect(defaultBotName()).toBe('Vexa');
    } finally {
      globalThis.fetch = orig;
    }
  });
});
