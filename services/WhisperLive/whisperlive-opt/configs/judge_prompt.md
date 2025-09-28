# LLM Judge Prompt Template

This template is used by the LLM judge to evaluate transcript quality against golden references.

## Prompt

You are evaluating a transcript against a golden reference for speech recognition quality.

Score each aspect on a 0-5 scale:

**FIDELITY (0-5)**: How well does the transcript capture the semantic meaning of the reference?
- 5: Perfect semantic match, all key information preserved
- 4: Very good match with minor semantic differences
- 3: Good match with some semantic variations
- 2: Partial match with significant semantic differences
- 1: Poor match with major semantic differences
- 0: No semantic relationship

**OMISSIONS (0-5)**: How much content is missing from the reference? (Lower is better)
- 0: No missing content
- 1: Minor omissions (single words/phrases)
- 2: Some omissions (multiple words/phrases)
- 3: Moderate omissions (sentences or concepts)
- 4: Major omissions (significant content)
- 5: Most content missing

**INSERTIONS (0-5)**: How much hallucinated content is added? (Lower is better)
- 0: No insertions
- 1: Minor insertions (single words)
- 2: Some insertions (multiple words)
- 3: Moderate insertions (phrases or sentences)
- 4: Major insertions (significant content)
- 5: Mostly hallucinated content

**OVERALL (0-5)**: Overall utility of the transcript for understanding the content
- 5: Excellent, fully usable
- 4: Very good, minor issues
- 3: Good, some limitations
- 2: Fair, significant limitations
- 1: Poor, major limitations
- 0: Unusable

Provide a 1-line rationale for your scoring.

Return ONLY valid JSON in this exact format:
{
  "fidelity": <score>,
  "omissions": <score>,
  "insertions": <score>,
  "overall": <score>,
  "rationale": "<explanation>"
}

## Usage Notes

- This prompt is designed to work with both OpenAI and Anthropic models
- The scoring is normalized to 0-1 scale in the evaluation code
- Caching is implemented to avoid repeated evaluations of the same transcript pairs
- The rationale field helps with debugging and understanding scoring decisions

## Example Response

```json
{
  "fidelity": 4,
  "omissions": 1,
  "insertions": 0,
  "overall": 4,
  "rationale": "High fidelity with minor word omissions, no hallucinations detected."
}
```
