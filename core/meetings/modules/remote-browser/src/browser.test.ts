import { persistentContextOptions } from './browser';

let failed = 0;
const check = (name: string, cond: boolean, detail = ''): void => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond ? '' : ` — ${detail}`}`);
  if (!cond) failed++;
};

const originalLocale = process.env.BOT_UI_LOCALE;
delete process.env.BOT_UI_LOCALE;

const defaultOptions = persistentContextOptions({ args: ['--no-sandbox'] });
check('default launch leaves executablePath unset',
  defaultOptions.executablePath === undefined, String(defaultOptions.executablePath));
check('default launch pins the product locale',
  defaultOptions.locale === 'en-US', String(defaultOptions.locale));
check('default launch keeps the canonical browser flags',
  defaultOptions.headless === false &&
  defaultOptions.ignoreDefaultArgs[0] === '--enable-automation' &&
  defaultOptions.args[0] === '--no-sandbox');

process.env.BOT_UI_LOCALE = 'hu-HU';
const envLocaleOptions = persistentContextOptions({ args: [] });
check('BOT_UI_LOCALE reaches the persistent context',
  envLocaleOptions.locale === 'hu-HU', String(envLocaleOptions.locale));

const explicitPath = '/opt/browser-a-b/chrome';
const overrideOptions = persistentContextOptions({
  args: ['--mute-audio'],
  headless: true,
  locale: 'fr-FR',
  executablePath: explicitPath,
});
check('explicit executablePath reaches Playwright launch options verbatim',
  overrideOptions.executablePath === explicitPath, String(overrideOptions.executablePath));
check('explicit locale and browser override coexist',
  overrideOptions.locale === 'fr-FR' &&
  overrideOptions.headless === true &&
  overrideOptions.args[0] === '--mute-audio');

if (originalLocale === undefined) delete process.env.BOT_UI_LOCALE;
else process.env.BOT_UI_LOCALE = originalLocale;

if (failed) {
  console.error(`\n❌ browser launch options: ${failed} check(s) failed`);
  process.exit(1);
}
console.log('\n✅ browser launch options: default pinned runtime + explicit eval override are both preserved.');
