# Environment Config

> **Why:** Procedures should not hardcode URLs, credentials, or VM specs.
> **What:** Flat key-value config imported by cookbooks via `use: env`.
> **How:** Swap this file for `env-staging.md` or `env-ci.md` to run against different targets.

## state

    REPO_URL          = "https://github.com/Vexa-ai/vexa.git"
    BRANCH            = "clean-aligned"

    VM_TYPE           = "g6-standard-6"
    VM_IMAGE          = "linode/ubuntu24.04"
    VM_REGION         = "us-ord"

    USER_EMAIL        = "test@vexa.ai"
    SPEAKER_EMAIL     = "tts@vexa.ai"

    TRANSCRIPTION_URL = "http://localhost:8085/health"

    CONFIDENCE_TARGET = 90

    GROUND_TRUTH = [
        {speaker: "Alice",   text: "Good morning everyone. Let's review the quarterly numbers."},
        {speaker: "Bob",     text: "Revenue increased by fifteen percent compared to last quarter."},
        {speaker: "Charlie", text: "Customer satisfaction score is ninety two percent."},
        {speaker: "Alice",   text: "We plan to hire three new engineers next month."},
        {speaker: "Bob",     text: "The marketing budget needs to be increased by twenty percent."},
        {speaker: "Charlie", text: "I agree. Our competitor launched a similar product last week."}
    ]

    GROUND_TRUTH_RAPID = [
        {speaker: "Alice", text: "So I've been looking at the quarterly numbers and I think we need to revisit our approach to customer acquisition. The cost per lead has gone up by about thirty percent since last quarter and that's not sustainable if we want to hit our annual targets.", duration: 30},
        {speaker: "Bob",   text: "Yeah I agree.", duration: 5},
        {speaker: "Alice", text: "The marketing team proposed shifting budget from paid search to content marketing and partnerships. They ran a pilot last month with two enterprise clients and the conversion rate was almost double what we see from Google Ads. The challenge is that content takes longer to produce and the feedback loop is slower so we won't see results for at least two quarters. But the unit economics are significantly better and we'd be building a moat instead of renting traffic.", duration: 60},
        {speaker: "Bob",   text: "What about the sales team? They're already struggling with lead quality.", duration: 10},
        {speaker: "Alice", text: "Right, that's the other piece. I talked to Sarah yesterday and she said the SDRs are spending about forty percent of their time qualifying leads that never convert. If we improve lead quality at the top of the funnel, even if volume drops initially, the sales team gets more productive.", duration: 30},
        {speaker: "Bob",   text: "Makes sense.", duration: 3},
        {speaker: "Alice", text: "OK let's schedule a working session with marketing and sales next week to align on the transition plan. I want concrete numbers, not just vibes.", duration: 15},
        {speaker: "Bob",   text: "I'll set it up.", duration: 3}
    ]
