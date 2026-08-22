import { describe, expect, it } from "vitest";
import {
  MINUTES_ONBOARDING_GREETING,
  MINUTES_PREP_GREETING,
  ONBOARDING_GREETING,
  onboardingGreeting,
} from "../actions";

describe("onboardingGreeting", () => {
  it("always uses personal onboarding for an explicit Personal setup", () => {
    expect(onboardingGreeting("personal", true, false)).toBe(ONBOARDING_GREETING);
    expect(onboardingGreeting("personal", true, true)).toBe(ONBOARDING_GREETING);
  });

  it("keeps contextual minutes onboarding for meeting entry points", () => {
    expect(onboardingGreeting("contextual", true, false)).toBe(MINUTES_PREP_GREETING);
    expect(onboardingGreeting("contextual", true, true)).toBe(MINUTES_ONBOARDING_GREETING);
    expect(onboardingGreeting("contextual", false, false)).toBe(ONBOARDING_GREETING);
  });
});
