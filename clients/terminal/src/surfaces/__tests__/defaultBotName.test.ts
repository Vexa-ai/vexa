import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { defaultBotName } from '../defaultBotName';

describe('defaultBotName', () => {
  beforeEach(() => {
    delete process.env.NEXT_PUBLIC_DEFAULT_BOT_NAME;
  });

  afterEach(() => {
    delete process.env.NEXT_PUBLIC_DEFAULT_BOT_NAME;
  });

  it('returns "Vexa" when env is unset', () => {
    expect(defaultBotName()).toBe('Vexa');
  });

  it('reads env at call time', () => {
    expect(defaultBotName()).toBe('Vexa');
    process.env.NEXT_PUBLIC_DEFAULT_BOT_NAME = 'MyBot';
    expect(defaultBotName()).toBe('MyBot');
    delete process.env.NEXT_PUBLIC_DEFAULT_BOT_NAME;
    expect(defaultBotName()).toBe('Vexa');
  });

  it('trims whitespace', () => {
    process.env.NEXT_PUBLIC_DEFAULT_BOT_NAME = '  Assistant  ';
    expect(defaultBotName()).toBe('Assistant');
  });
});
