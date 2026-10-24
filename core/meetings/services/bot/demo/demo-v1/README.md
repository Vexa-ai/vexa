# demo-v1 — the first demo script

Two voices, Maya and Leo, in seven alternating turns (about 53 s of speech, about a minute with the
gaps): a short planning call — a launch date, a pricing change, who follows up — that ends by pointing
the person at Claude for a summary and at their next real meeting. The words are in `script.json`.

The audio was synthesized by Vexa from that text with Deepgram Aura-2 (`aura-2-thalia-en` for Maya,
`aura-2-apollo-en` for Leo), 16 kHz mono. To change the words, regenerate every turn from the edited
`script.json` with the same voices and keep the file names.
