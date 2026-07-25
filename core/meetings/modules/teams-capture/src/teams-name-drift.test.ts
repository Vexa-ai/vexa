/**
 * Teams participant-name resolver regression.
 *
 * The fixture uses only opaque atomic classes for the visible name leaf: no
 * data-tid, title, aria label, or stable name class can satisfy the fast path.
 *
 * Run:
 *   pnpm --filter @vexa/teams-capture exec tsx src/teams-name-drift.test.ts
 */
import { extractTeamsSpeakerName } from './msteams-speakers.js';

let checks = 0;
function check(name: string, condition: boolean, detail = ''): void {
  if (!condition) throw new Error(`${name}${detail ? `: ${detail}` : ''}`);
  console.log(`  ✅ ${name}`);
  checks++;
}

class ElementFixture {
  readonly children: ElementFixture[] = [];

  constructor(
    readonly tagName: string,
    private readonly attrs: Record<string, string> = {},
    readonly textContent = '',
  ) {}

  add(...children: ElementFixture[]): this {
    this.children.push(...children);
    return this;
  }

  getAttribute(name: string): string | null {
    return this.attrs[name] ?? null;
  }

  private descendants(): ElementFixture[] {
    const result: ElementFixture[] = [];
    const visit = (element: ElementFixture): void => {
      for (const child of element.children) {
        result.push(child);
        visit(child);
      }
    };
    visit(this);
    return result;
  }

  private matches(selector: string): boolean {
    const match = selector.match(
      /^([a-z0-9]*)(?:\[([a-z-]+)(?:\*?=)?(?:"([^"]*)")?\]|\.([\w-]+))?$/i,
    );
    if (!match) return false;
    const [, tag, attribute, attributeValue, className] = match;
    if (tag && this.tagName.toLowerCase() !== tag.toLowerCase()) return false;
    if (className) {
      return (this.attrs.class ?? '').split(/\s+/).includes(className);
    }
    if (attribute) {
      const value = this.getAttribute(attribute);
      if (value === null) return false;
      return attributeValue ? value.includes(attributeValue) : true;
    }
    return Boolean(tag);
  }

  querySelector(selector: string): ElementFixture | null {
    return this.descendants().find((element) => element.matches(selector)) ?? null;
  }

  querySelectorAll(selector: string): ElementFixture[] {
    if (selector === '*') return this.descendants();
    return this.descendants().filter((element) => element.matches(selector));
  }
}

const leaf = (text: string, className: string): ElementFixture =>
  new ElementFixture('div', { class: className }, text);

const atomicTile = new ElementFixture('div', {
  'data-tid': 'video-tile',
  class: 'fui-Primitive ___1r8x2k0',
}).add(
  leaf('05:14', '___clock f1timer'),
  leaf('Mute', '___mic f1control'),
  new ElementFixture('div', { class: '___1504rl1 f1euv43f' }).add(
    leaf('Anna Rivera', '___12zni01 f1cmbuwj fv6wr3j'),
  ),
);

check(
  'RED control: explicit selectors cannot resolve the atomic-hash-only name leaf',
  extractTeamsSpeakerName(atomicTile as unknown as HTMLElement, {
    structuralFallback: false,
  }) === '',
);
check(
  'GREEN: structural leaf fallback resolves the visible atomic-hash name',
  extractTeamsSpeakerName(atomicTile as unknown as HTMLElement) === 'Anna Rivera',
);

const fastPathTile = new ElementFixture('div').add(
  new ElementFixture('span', { title: 'Boris Levin' }, 'Boris Levin'),
);
check(
  'stable explicit selector remains the preferred fast path',
  extractTeamsSpeakerName(fastPathTile as unknown as HTMLElement, {
    structuralFallback: false,
  }) === 'Boris Levin',
);

for (const value of ['05:14', 'Mic', 'Mute', 'Unmute', 'Microphone', '12345']) {
  const controlOnly = new ElementFixture('div').add(leaf(value, '___atomic'));
  check(
    `negative: ${value} is not a participant name`,
    extractTeamsSpeakerName(controlOnly as unknown as HTMLElement) === '',
  );
}

const unresolved = new ElementFixture('div').add(
  new ElementFixture('div', { class: '___container' }).add(
    leaf('', '___empty'),
  ),
);
check(
  'negative: an unresolved tile remains unresolved',
  extractTeamsSpeakerName(unresolved as unknown as HTMLElement) === '',
);

console.log(
  `\n✅ teams name drift: ${checks} checks passed — atomic-hash names resolve without timer/control fabrication.`,
);
