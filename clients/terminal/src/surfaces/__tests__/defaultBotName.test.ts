import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { defaultBotName } from '../defaultBotName';

describe('defaultBotName', () => {
  beforeEach(() => {
    delete process.env.NEXT_PUBLIC_DEFAULT_BOT_NAME;
  });

  afterEach(() => {
    delete process.env.NEXT_PUBLIC_DEFAULT_BOT_NAME;
  });

  it('returns undefined when env is unset (JSON.stringify will omit bot_name)', () => {
    expect(defaultBotName()).toBeUndefined();
    expect(JSON.stringify({ platform: 'google_meet', bot_name: defaultBotName() })).toBe(
      '{"platform":"google_meet"}',
    );
  });

  it('reads NEXT_PUBLIC_DEFAULT_BOT_NAME at call time when set', () => {
    process.env.NEXT_PUBLIC_DEFAULT_BOT_NAME = 'MyBot';
    expect(defaultBotName()).toBe('MyBot');
    delete process.env.NEXT_PUBLIC_DEFAULT_BOT_NAME;
    expect(defaultBotName()).toBeUndefined();
  });

  it('trims whitespace', () => {
    process.env.NEXT_PUBLIC_DEFAULT_BOT_NAME = '  Assistant  ';
    expect(defaultBotName()).toBe('Assistant');
  });
});
