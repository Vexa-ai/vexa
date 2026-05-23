# Ground Truth Speaker Conversation

Use this canonical script for the default two-speaker deployment test. It is written as a short, named dialogue between two meeting participants. The turns are intentionally short so the bots do not talk over each other, while still giving the listener transcript enough anchors to score content, turn order, speaker attribution, and multilingual TTS.

## Speaker Setup

- Speaker 1 participant name: `Maya Chen`
- Speaker 1 id: `speaker-1`
- Speaker 1 default voice: `en_US-amy-medium`
- Speaker 2 participant name: `Leo Santos`
- Speaker 2 id: `speaker-2`
- Speaker 2 default voice: `en_US-danny-low`
- Listener label: `listener-test`

## Validation Targets

- Expected speaker turns: 16
- Expected content: the listener transcript should preserve the product explanation, named participants, multilingual checkpoints, and operational details.
- Expected attribution: each turn should be attributed to the correct visible participant name when speaker labels are available.
- Key anchors: `Maya Chen`, `Leo Santos`, `meeting intelligence`, `Google Meet`, `Microsoft Teams`, `API gateway`, `speaker labels`, `webhooks`, `self-hosted`, `recordings`, `real time`, `developer workflow`, `support team`, `Spanish checkpoint`, `French checkpoint`, `Portuguese checkpoint`, `case CASE_ID`, `run RUN_ID`, `final takeaway`

## Script

1. `speaker-1|Maya Chen|en_US-amy-medium`: Hi Leo, this is Maya Chen starting case CASE_ID for run RUN_ID. I will keep each turn short so we can test meeting intelligence without overlapping audio.

2. `speaker-2|Leo Santos|en_US-danny-low`: Thanks Maya. This is Leo Santos. I will answer you directly, and together we will test whether Vexa hears two named speakers in Google Meet.

3. `speaker-1|Maya Chen|en_US-amy-medium`: First checkpoint: a developer calls the API gateway, asks for a bot to join, and Vexa handles browser control, audio capture, and cleanup.

4. `speaker-2|Leo Santos|en_US-danny-low`: I hear you. The second checkpoint is speaker labels. The listener transcript should separate Maya Chen from Leo Santos instead of mixing us together.

5. `speaker-1|Maya Chen|es_ES-davefx-medium`: Spanish checkpoint: Buenos dias, Leo. Vexa convierte reuniones de Google Meet y Microsoft Teams en inteligencia de reuniones en tiempo real.

6. `speaker-2|Leo Santos|fr_FR-siwis-medium`: French checkpoint: Bonjour Maya. Les webhooks doivent annoncer le statut de la reunion, les erreurs, et la transcription finale.

7. `speaker-1|Maya Chen|en_US-amy-medium`: Nice. Back in English, a support team can use Vexa to capture customer calls, action items, objections, and promised follow ups.

8. `speaker-2|Leo Santos|en_US-danny-low`: And a developer workflow can fetch transcripts, stop the bot, store recordings, and connect the output to an internal product.

9. `speaker-1|Maya Chen|pt_BR-faber-medium`: Portuguese checkpoint: Ola Leo, infraestrutura self-hosted ajuda equipes a controlar logs, armazenamento e retencao de gravacoes.

10. `speaker-2|Leo Santos|en_US-danny-low`: Good handoff, Maya. The self-hosted point matters because teams want reliable logs and predictable data boundaries.

11. `speaker-1|Maya Chen|en_US-amy-medium`: For scoring, I am responsible for meeting intelligence, the API gateway, self-hosted deployment, and the final takeaway.

12. `speaker-2|Leo Santos|en_US-danny-low`: For scoring, I am responsible for Google Meet, Microsoft Teams, speaker labels, webhooks, and recordings.

13. `speaker-1|Maya Chen|en_US-amy-medium`: Leo, please confirm the operations report should include bot lifecycle, first transcript time, webhook delivery, and stop status.

14. `speaker-2|Leo Santos|en_US-danny-low`: Confirmed, Maya. The report should also mention whether the dashboard updated in real time and whether playback or transcript artifacts are available.

15. `speaker-1|Maya Chen|en_US-amy-medium`: My final takeaway is that Vexa turns live meeting audio into programmable meeting intelligence for real products.

16. `speaker-2|Leo Santos|en_US-danny-low`: My final takeaway is that this named two speaker demo should prove the listener heard Maya and Leo clearly. End of podcast.

## Scoring Guidance

- Content accuracy: count how many expected turns or key anchors appear in the listener transcript. Report exact match only when transcript text is close enough to the scripted turn; otherwise report partial match.
- Speaker identification quality: compare the expected speaker for each turn with the listener transcript's speaker label. Report correct, swapped, missing, or unknown labels.
- Turn order: verify the listener transcript preserves the alternating speaker order well enough to reconstruct the conversation.
- Multilingual coverage: check whether the Spanish, French, and Portuguese checkpoints are present or partially present in the listener transcript.
- Failure handling: if the listener transcript has no speaker labels, do not score speaker identification as passed. Report content accuracy separately and mark speaker identification unavailable or failed, depending on the deployment's expected feature set.
