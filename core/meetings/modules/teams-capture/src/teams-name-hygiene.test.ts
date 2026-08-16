/**
 * Name hygiene at the ONE guard — the door every name in this package walks through.
 *
 * Both failures this pins were live, on the m34 meeting, and both produced a CONFIDENT WRONG NAME
 * rather than a blank, which is the difference between a gap someone notices and a lie nobody does:
 *
 *   • the roster listed "Vexa (Unverified)" — our own bot — because the self filter compared raw
 *     strings and the bot joins as "Vexa". That name was then handed to a human by elimination.
 *   • Teams attributed 50 captions to "Unknown user", its placeholder for a participant it cannot
 *     identify, and that string became a speaker label on the founder's transcript.
 *
 * Run: npx tsx src/teams-name-hygiene.test.ts
 */
import {
  isGeneratedDefaultBotDisplayName, isTeamsDisplayNameCandidate, isSelfDisplayName,
  normalizeDisplayNameForIdentity,
} from './msteams-speakers.js';

let failed = 0;
const check = (name: string, cond: boolean, detail?: string): void => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond || !detail ? '' : ` — ${detail}`}`);
  if (!cond) failed++;
};

// ── the bot's own name, however Teams dresses it ─────────────────────────────────────────────────
for (const variant of ['Vexa', 'Vexa (Unverified)', 'vexa (unverified)', 'VEXA (Guest)', 'Vexa 2', 'Vexa (2)', 'Vexa (Bot)']) {
  check(`self: "${variant}" is recognised as our own bot`, isSelfDisplayName(variant, 'Vexa'), variant);
}
check('self: a DIFFERENT person whose name merely starts the same is not us',
  !isSelfDisplayName('Vexana Petrova', 'Vexa'));
check('self: an empty bot name never matches anybody', !isSelfDisplayName('Anyone', ''));
check('identity normalisation strips the qualifier but the DISPLAY name is untouched',
  normalizeDisplayNameForIdentity('Leo (Unverified)') === 'leo'
  && isTeamsDisplayNameCandidate('Leo (Unverified)'));

// ── meeting-api's generated fallback bot identity ────────────────────────────────────────────────
for (const generated of [
  'VexaBot-8f264c', 'VexaBot-8f264c (Unverified)', 'VexaBot-8f264c (Guest)',
  'VexaBot-8f264c 2', 'VexaBot-8f264c (2)', 'VexaBot-8f264c (Gość) 2',
  'VexaBot-8f264c (外部) (2)',
]) {
  check(`generated bot: "${generated}" is recognized exactly`,
    isGeneratedDefaultBotDisplayName(generated), generated);
  check(`generated bot: "${generated}" can never become a human name`,
    !isTeamsDisplayNameCandidate(generated), generated);
}
check('stacked Teams suffixes still compare as the local bot',
  isSelfDisplayName('Vexa (Guest) 2', 'Vexa'));

// ── the platform's placeholders ──────────────────────────────────────────────────────────────────
for (const placeholder of [
  'Unknown user', 'unknown user', 'Unknown User', 'Unknown', 'Guest', 'Anonymous',
  'Unbekannter Benutzer', 'Utilisateur inconnu', 'Usuario desconocido', 'Неизвестный пользователь',
  'Unknown user (Guest)',
]) {
  check(`placeholder: "${placeholder}" can never become a name`, !isTeamsDisplayNameCandidate(placeholder), placeholder);
}
check('m26132: a bare lowercase media label cannot become Julian\'s name',
  !isTeamsDisplayNameCandidate('datenanalyse'));

// ── and the humans still get through ─────────────────────────────────────────────────────────────
for (const real of [
  'Dmitry Grankin', 'leo (Unverified)', 'Anne-Marie', 'Jean-Luc Picard', 'Максим', 'Bo',
  'Vexa Petrova', 'Vexana Petrova', 'Robin Botman', 'Assistant Smith', 'VexaBot Smith',
  'VexaBot-8f264', 'VexaBot-8f264cc', 'VexaBot-8f26zz',
]) {
  check(`human: "${real}" is still a name`, isTeamsDisplayNameCandidate(real), real);
}

if (failed) { console.error(`\n❌ teams-name-hygiene: ${failed} check(s) FAILED.`); process.exit(1); }
console.log('\n✅ teams-name-hygiene: our own bot and the platform\'s placeholders can never become speakers, and real names are untouched.');
