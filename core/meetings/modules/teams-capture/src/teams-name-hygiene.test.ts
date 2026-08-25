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
// ── bare lowercase: refused on a tile, admitted when the roster says it is a person ──────────────
// The refusal exists because a tile's name slot can hold a role or topic label; it must not exist so
// hard that a participant who genuinely calls themselves `leo` is published as Speaker A.
check('a bare lowercase label seen only on a tile cannot become a name',
  !isTeamsDisplayNameCandidate('datenanalyse'));
check('a bare lowercase label stays refused when the roster lists OTHER people',
  !isTeamsDisplayNameCandidate('datenanalyse', { rosterNames: ['Julian Weber', 'Dmitry Grankin'] }));
check('bare "leo" alone is not yet a name', !isTeamsDisplayNameCandidate('leo'));
check('bare "leo" IS a name once the roster panel lists it',
  isTeamsDisplayNameCandidate('leo', { rosterNames: ['leo', 'Dmitry Grankin'] }));
check('the roster panel itself may read a bare lowercase row as a person',
  isTeamsDisplayNameCandidate('leo', { rosterAuthoritative: true }));
check('a non-Latin bare lowercase name is corroborated the same way',
  !isTeamsDisplayNameCandidate('марина')
  && isTeamsDisplayNameCandidate('марина', { rosterNames: ['марина'] }));
check('corroboration compares identities, so a qualified roster row vouches for the bare tile',
  isTeamsDisplayNameCandidate('leo', { rosterNames: ['Leo (Guest)'] }));
check('corroboration lifts ONLY the bare-handle rule — a control label on the roster is still not a name',
  !isTeamsDisplayNameCandidate('mic_off', { rosterAuthoritative: true })
  && !isTeamsDisplayNameCandidate('unknown user', { rosterAuthoritative: true })
  && !isTeamsDisplayNameCandidate('video-stream-2', { rosterAuthoritative: true })
  && !isTeamsDisplayNameCandidate('vexabot-8f264c', { rosterAuthoritative: true }));

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
