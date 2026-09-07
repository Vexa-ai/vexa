export interface NumberSignal {
  text: string;
  value?: number;
  context?: string;
  at?: number;
  end?: number;
}

const DOMAIN_CORRECTIONS: [RegExp, string][] = [
  [/\bDeep\s+Sea\b/gi, "DeepSeek"],
  [/\bDeep\s+Seek\b/gi, "DeepSeek"],
  [/\bDeepMIND\b/g, "DeepMind"],
  [/\bCloud\s+Code\b/gi, "Claude Code"],
  [/\bOpen\s+Router\b/gi, "OpenRouter"],
  [/\bEntropic\b/gi, "Anthropic"],
  [/\bAnthropoc\b/gi, "Anthropic"],
  [/\bUniversity\s+Chicago\b/gi, "University of Chicago"],
  [/\bYalna\s+Kunz\b/gi, "Yann LeCun"],
  [/\bYann\s+Le\s+Cun\b/gi, "Yann LeCun"],
];

const ORG_CONTRACTION_RE = /\b(China|Google|OpenAI|DeepMind|DeepSeek|Anthropic|Microsoft|Meta|Nvidia|Apple|Amazon)'s\s+([a-z]+ing)\b/gi;

const MONEY_RE = /[$€£]\s*\d[\d,]*(?:\.\d+)?\s*(?:billion|million|thousand|bn|mm|[kmb])?\+?/gi;
const QUARTER_RE = /\bQ[1-4](?:\s*(?:FY)?\d{2,4})?\b/gi;
const YEAR_RE = /\b(?:19|20)\d{2}\b/g;
const METRIC_RE = /\b\d[\d,]*(?:\.\d+)?\s*(?:%|billion|million|thousand|bn|mm|[kmb]\b|x\b|users?|seats?|licenses?|customers?|employees?|companies?|teams?|repos?|issues?|tickets?|ms|mb|gb|tb)(?=$|[\s,.;:!?)])/gi;

export function cleanTranscriptText(text: string): string {
  let out = text.replace(/[“”]/g, '"').replace(/[‘’]/g, "'").replace(/\s+/g, " ").trim();
  out = out.replace(/([$€£]?\s*\d[\d,]*(?:\.\d+)?\s+)([bBmM])\s+illion\b/g, (_m, amount: string, unit: string) => {
    const suffix = unit.toLowerCase() === "b" ? "billion" : "million";
    return `${amount}${suffix}`;
  });
  for (const [pattern, replacement] of DOMAIN_CORRECTIONS) out = out.replace(pattern, replacement);
  out = out.replace(ORG_CONTRACTION_RE, "$1 is $2");
  return out.replace(/\s+([,.!?;:])/g, "$1").replace(/([^\d])([.!?])(?=\S)/g, "$1$2 ");
}

function valueOf(label: string): number | undefined {
  const cleaned = label.replace(/[^0-9.-]/g, "");
  const value = cleaned ? Number(cleaned) : undefined;
  return Number.isFinite(value) ? value : undefined;
}

function contextFor(label: string): string {
  const lower = label.toLowerCase();
  if (/[$€£]/.test(label)) return "Money";
  if (/%/.test(label)) return "Rate";
  if (/^q[1-4]/i.test(label)) return "Timeline";
  if (/^(?:19|20)\d{2}$/.test(label)) return "Year";
  if (/seats?|licenses?|users?|customers?/.test(lower)) return "Seats";
  if (/ms|mb|gb|tb/.test(lower)) return "Technical";
  if (/billion|million|thousand|\bbn\b|\bmm\b|\b[kmb]\b|employees?|companies?|teams?|repos?|issues?|tickets?/.test(lower)) return "Scale";
  if (/\bx\b/.test(lower)) return "Multiple";
  return "Number";
}

function addSignal(acc: NumberSignal[], seen: Set<string>, text: string, at: number): void {
  const label = text.replace(/[\s,.]+$/, "").trim();
  if (!label) return;
  const key = label.toLowerCase();
  if (seen.has(key)) return;
  if (acc.some((item) => item.at != null && item.end != null && at < item.end && at + label.length > item.at)) return;
  seen.add(key);
  acc.push({ text: label, value: valueOf(label), context: contextFor(label), at, end: at + label.length });
}

export function extractNotableNumberSpans(text: string): NumberSignal[] {
  const clean = cleanTranscriptText(text);
  const out: NumberSignal[] = [];
  const seen = new Set<string>();
  for (const re of [MONEY_RE, QUARTER_RE, YEAR_RE, METRIC_RE]) {
    re.lastIndex = 0;
    for (const match of clean.matchAll(re)) addSignal(out, seen, match[0], match.index ?? 0);
  }
  return out.sort((a, b) => (a.at ?? 0) - (b.at ?? 0));
}

export function extractNotableNumbers(texts: string[], limit = 24): NumberSignal[] {
  const out: NumberSignal[] = [];
  const seen = new Set<string>();
  for (const text of texts) {
    for (const signal of extractNotableNumberSpans(text)) {
      const key = signal.text.toLowerCase();
      if (seen.has(key)) continue;
      seen.add(key);
      out.push({ text: signal.text, value: signal.value, context: signal.context });
    }
  }
  return out.slice(-limit);
}
