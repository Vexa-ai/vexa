import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { defaultBotName } from '../index';

describe('defaultBotName', () => {
  beforeEach(() => {
    delete process.env.DEFAULT_BOT_NAME;
  });

  afterEach(() => {
    delete process.env.DEFAULT_BOT_NAME;
  });

  it('returns "Vexa Join Layer" when env is unset', () => {
    expect(defaultBotName()).toBe('Vexa Join Layer');
  });

  it('reads env at call time', () => {
    expect(defaultBotName()).toBe('Vexa Join Layer');
    process.env.DEFAULT_BOT_NAME = 'MyBot';
    expect(defaultBotName()).toBe('MyBot');
    delete process.env.DEFAULT_BOT_NAME;
    expect(defaultBotName()).toBe('Vexa Join Layer');
  });

  it('trims whitespace', () => {
    process.env.DEFAULT_BOT_NAME = '  Assistant  ';
    expect(defaultBotName()).toBe('Assistant');
  });
});
