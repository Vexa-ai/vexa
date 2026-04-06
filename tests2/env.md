# Environment Config

> **Why:** Procedures should not hardcode URLs, credentials, or VM specs.
> **What:** Flat key-value config imported by cookbooks via `use: env`.
> **How:** Swap this file for `env-staging.md` or `env-ci.md` to run against different targets.

## state

    REPO_URL          = "https://github.com/Vexa-ai/vexa.git"
    BRANCH            = "clean"

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
