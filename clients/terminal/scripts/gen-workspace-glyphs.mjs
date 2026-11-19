#!/usr/bin/env node
// gen-workspace-glyphs.mjs — the STANDARD WORKSPACE ICONS (founder 2026-10-18: "let's pick icons from a
// large selection of standard icons at creation unless changed").
//
// One curated list below — an icon of the outline set the product already draws with (lucide-react,
// ISC), plus the words that name what a workspace about it is for — is written out twice from the
// installed lucide-react's own icon nodes, so the client and the server draw the same strokes and match
// the same words:
//
//   clients/terminal/src/platform/workspaceGlyphs.data.ts   the picker, the client's matcher, the tile
//   core/agent/control_plane/workspace_glyphs.json          the server's matcher and its SVG fallback
//
// A keyword with a leading "~" is WEAK (a generic word such as "club" or "company": it only decides
// when nothing specific matched). Run from clients/terminal:  node scripts/gen-workspace-glyphs.mjs
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const TERMINAL = path.resolve(HERE, "..");
const ICONS = path.join(TERMINAL, "node_modules/lucide-react/dist/esm/icons");

// name: keywords (comma-separated; "~" = weak). Order is the picker's order and the tie-break.
const LIST = `
map: roadmap, planning, plan, plans, strategy, journey, route map
milestone: milestone, release, releases, launch plan
kanban: kanban, board, sprint, backlog, tasks
list-checks: checklist, todo, tasks, operations, ops
target: goals, goal, okr, okrs, objectives, focus
flag: milestones, priorities, flag
rocket: launch, launches, startup, ship, shipping product
lightbulb: ideas, idea, brainstorm, innovation, inventions
compass: direction, vision, explore, exploration, orientation
package: ~product, ~products, packages, inventory
layers: platform, architecture, stack
layout-dashboard: dashboard, dashboards, overview, cockpit
puzzle: integrations, puzzle, puzzles, plugins
book-open: handbook, guide, guides, manual, wiki, docs, documentation, knowledge, playbook, reading, knowledge base
book-marked: book club, literature, reading list, books
library: library, archive, references, research library
notebook-pen: notes, journal, diary, notebook, meeting notes
file-text: documents, document, policies, contracts, specs, spec, paperwork
scroll-text: policy, terms, charter, constitution, history
newspaper: news, press, newsletter, media, blog, journalism
graduation-cap: school, education, course, courses, training, learning, onboarding, university, study, students, academy
pen-line: writing, editorial, content, copy, copywriting, drafts
languages: translation, translations, languages, localization, language
chart-line: growth, metrics, kpi, kpis, trends, analytics, performance
chart-column: reports, reporting, statistics, stats, data analysis
chart-pie: budget, allocation, market share, budgets
trending-up: ~growth, sales, revenue, gains, scale up
chart-candlestick: trading, stocks, markets, investing, portfolio, investments
landmark: bank, banking, nationalbank, central bank, government, institution, ministry, oenb, ecb, regulator, regulators, treasury, public sector
banknote: payments, cash, money, payroll, salaries
wallet: wallet, expenses, spending, personal finance
piggy-bank: savings, fundraising, raise, seed round, fund
circle-dollar-sign: finance, revenue, pricing, monetization, income, dollar
euro: euro, eu funding
bitcoin: bitcoin, crypto, blockchain, web3
receipt: invoices, invoice, billing, receipts, accounting
calculator: accounting, tax, taxes, bookkeeping, calculations
briefcase: business, work, consulting, clients, career, jobs, ~company
building-2: corporate, enterprise, headquarters, offices, ~company, ~org
handshake: partners, partner, partnership, partnerships, deals, crm, relationships, alliances
scale: legal, law, compliance, lawyer, lawyers, justice
gavel: court, auction, litigation, rulings
shield-check: security, audit, audits, privacy, risk, safety
lock: secrets, access, private, confidential
key-round: keys, credentials, passwords
users: team, teams, community, people, members, staff, ~club, ~group, ~crew
user-plus: hiring, recruiting, recruitment, candidates, talent, interviews
id-card: hr, people ops, employees, human resources
contact: contacts, directory, address book
megaphone: marketing, announcements, campaigns, campaign, pr, outreach, advertising, ads
mail: email, emails, inbox zero, mail, mailing
message-circle: chat, conversations, feedback, community chat
headphones: support, helpdesk, customer service, listening
life-buoy: help, rescue, customer success
store: shop, store, retail, ecommerce, boutique
shopping-cart: shopping, orders, purchases, procurement, groceries
shopping-bag: fashion, merch, merchandise
tag: tags, labels, deals, discounts
gift: gifts, gift, birthday gifts, presents, rewards
truck: delivery, logistics, shipping, transport, fleet, deliveries
code: code, engineering, development, dev, software, programming, developers
terminal: devops, cli, scripts, command line, sre
git-branch: git, repository, repositories, versions, open source
bug: bugs, qa, testing, issues, debugging, insects
server: infrastructure, infra, servers, backend, hosting
database: data, database, databases, warehouse data, sql, datasets
cloud: cloud, saas, cloud services
cpu: hardware, chips, compute, electronics
bot: bots, automation, agents, agent, automations, assistant
brain: ai, ml, machine learning, thinking, psychology, neuroscience, mind
sparkles: magic, creativity, ~new, ~misc
wrench: maintenance, tools, repairs, fixes, mechanics
settings: settings, admin, config, configuration, operations
plug: api, apis, connectors, plugins integrations
globe: website, web, international, global, world, websites
smartphone: mobile, app, apps, ios, android, phone app
laptop: laptop, remote work, computers, it
monitor: desktop, screens, displays
wifi: network, internet, connectivity
network: networking, org chart, connections, graph
flask-conical: science, lab, labs, chemistry, experiments, experiment
microscope: biology, research, microbiology, pathology
atom: physics, science club, quantum
dna: genetics, biotech, bio, genomics
stethoscope: health, medical, doctor, doctors, clinic, healthcare, medicine
heart-pulse: fitness, wellness, cardio, wellbeing, vitals
pill: pharma, pharmacy, medication, drugs
hospital: hospital, hospitals, patients
ambulance: emergency, ambulance, ems, first responders
syringe: vaccines, vaccination, injections
dumbbell: gym, workout, workouts, strength, weightlifting
apple: nutrition, diet, fruit, healthy eating
sprout: garden, gardening, plants, plant, seedlings, sustainability, seeds, ~growth
leaf: nature, eco, environment, green, botany, ecology
trees: forest, forestry, park, parks, woods
tree-pine: christmas, pine, conifer
flower-2: flowers, florist, flower, bloom, ~garden
mountain: mountains, hiking, climbing, outdoors, adventure, trekking
mountain-snow: skiing, ski, alps, snowboarding
tent: camping, campsite, festival, festivals
sun: summer, solar, sunshine, sunny
cloud-sun: weather, forecast, climate data
snowflake: winter, snow, cold
waves: ocean, sea, surf, surfing, beach, marine
droplet: water, liquid, hydration, plumbing
flame: fire, bbq, barbecue, heat
zap: energy, electricity, power, utilities, electric
wind: wind, turbines, air
recycle: recycling, waste, circular economy, zero waste
earth: earth, planet, climate, geography, climate change
bird: birds, birdwatching, birding, ornithology
dog: dogs, dog, puppy, puppies, dog walking
cat: cats, cat, kittens
paw-print: pets, pet, animals, vet, veterinary, animal shelter
fish: fishing, fish, aquarium, angling
rabbit: rabbit, rabbits, bunnies
squirrel: wildlife, squirrel
house: home, house, family, household, real estate, property, rent, apartment, housing
sofa: furniture, interior, interior design, living room
bed: sleep, bedroom, rooms
hammer: construction, diy, renovation, building, carpentry, repairs home
paint-roller: painting, decorating, redecoration
palette: design, art, branding, brand, colours, colors, creative, artists
pencil-ruler: architecture, architects, drafting, blueprints
shirt: clothing, apparel, clothes, wardrobe
scissors: crafts, crafting, sewing, haircut, hair
baby: baby, babies, parenting, kids, children, newborn
heart: love, wedding, dating, relationship, care
hand-heart: charity, nonprofit, non profit, donations, volunteering, ngo, volunteers
church: church, faith, religion, parish, congregation
party-popper: party, parties, events, celebration, offsite, celebrations
cake: birthday, birthdays, bakery, baking, cakes
chef-hat: cooking, recipes, recipe, kitchen, chef, meal prep
utensils: restaurant, restaurants, food, dining, meals, catering
coffee: coffee, cafe, breaks, coffee chats
wine: wine, wines, tasting, vineyard, sommelier
beer: beer, pub, brewery, brewing
pizza: pizza, pizza night
cooking-pot: soup, stew, cookbook
croissant: breakfast, pastry, patisserie
leafy-green: vegetables, greens, salad, farm to table
plane: travel, flights, trips, trip, vacation, holiday, holidays, aviation
car: cars, auto, automotive, driving, car pool
bike: cycling, bike, bikes, bicycle, bicycles
bus: transit, bus, public transport
train-front: trains, train, rail, railway, commute
ship: maritime, cruise, ships, ferry
sailboat: sailing, boat, boats, yacht, regatta
map-pin: locations, places, local, venue, venues, location
luggage: luggage, packing, relocation, moving
hotel: hotel, hotels, hospitality, accommodation, airbnb
tickets: tickets, concerts, concert, gigs
music: music, band, songs, choir, playlist, musicians
guitar: guitar, guitars, rock band
piano: piano, music lessons
mic: podcast, interviews, interview, voice, speaking, talks, recording, speakers
camera: photography, photos, photo, camera, photographers
film: film, films, movies, cinema, movie club
clapperboard: production, filmmaking, video production, studio
video: video, videos, streaming, youtube, webinars
image: images, gallery, assets, pictures
gamepad-2: games, gaming, esports, video games
dices: board games, tabletop, dnd, dungeons
drama: theatre, theater, drama, acting, improv
tv: tv, television, shows, series
trophy: competition, competitions, awards, league, tournament, sports, winners, championship
medal: medals, achievements, olympics
footprints: running, walking, steps, marathon, runners
swords: fencing, battle, rivals, competitors, competitor analysis
backpack: backpacking, students backpack, school trip
pencil: sketches, drafting, homework
factory: manufacturing, factory, production line, industrial, plant operations
warehouse: warehouse, storage, fulfilment, fulfillment
tractor: farming, agriculture, farm, farmers
wheat: grain, harvest, crops, agronomy
hard-hat: site, construction site, safety equipment, contractors
construction: roadworks, under construction, infrastructure projects
anchor: port, harbor, harbour, navy
castle: castle, medieval, kingdom, heritage
container: containers, docker, kubernetes, shipping containers
folder: files, folder, folders, ~general, ~stuff, ~misc
inbox: intake, requests, inbox, triage
archive: archive, archives, old, past
star: favorites, favourites, starred, important, vip
bookmark: bookmarks, saved, read later
gem: premium, jewelry, jewellery, luxury, gems
crown: leadership, executive, executives, board, ceo, royal, founders
eye: monitoring, observing, observability, watch list
search: investigation, discovery, search, seo, sourcing
telescope: astronomy, exploration, stargazing, foresight
orbit: space, astronomy club, satellites
moon: night, moon, night shift, lunar
clock: time, timesheets, hours, timekeeping, time tracking
hourglass: deadlines, deadline, waiting list, countdown
alarm-clock: reminders, mornings, wake up
repeat: routines, routine, recurring, habits, habit
sticky-note: memos, post its, scratchpad, sticky notes
clipboard-list: forms, surveys, survey, inspections, questionnaires
clipboard-check: approvals, reviews, review, sign off, quality checks
file-spreadsheet: spreadsheets, spreadsheet, excel, sheets, financial model
presentation: presentations, pitch, deck, slides, investors, investor relations, board meeting
calendar-days: calendar, schedule, events calendar, agenda, timetable
calendar-clock: meetings, meeting, scheduling, sync, standups, standup, weekly sync, calls
phone: phone, telephony, sales calls, cold calls
award: certification, certifications, recognition
badge-check: verification, quality, verified, quality assurance
thumbs-up: likes, testimonials, reviews customer, nps
smile: happiness, fun, culture, team culture, social
shield: insurance, protection, defense, defence
umbrella: insurance policy, rainy day, coverage
siren: incidents, incident, emergency response, alerts, on call, outages
bell: notifications, reminders bell, alerting
triangle-alert: risks, warnings, issues log, risk register
route: routes, delivery routes, itinerary
signpost: directions, guidance, navigation, wayfinding
percent: offers, promotions, interest rates, vat
gauge: speed, efficiency, benchmarks, benchmarking
funnel: funnel, pipeline, conversion, leads, lead gen, sales pipeline
magnet: attraction, lead magnet, inbound
share-2: social media, sharing, socials, social
at-sign: mentions, handles
hash: channels, hashtags, slack
rss: feeds, rss, podcasting feeds
link: links, bookmarks links, references links
fingerprint-pattern: identity, biometrics, authentication, auth
user-check: attendance, verified people, onboarding checklist
accessibility: accessibility, a11y, inclusion, inclusive design
hand-helping: assistance, mentoring, mentorship, coaching
type: typography, fonts, font
spell-check: proofreading, editing, grammar, copy edit
wand-sparkles: wizard, assistant magic, prompts, prompt engineering
box: boxes, storage box, moving boxes
shapes: shapes, geometry, abstract, ~sandbox
hexagon: hexagon, honeycomb, bees, beekeeping
infinity: forever, infinite, loop
glasses: optician, eyewear, reading glasses
watch: watches, wearables, wristwatch
`;

const GENERIC = ["folder", "layers", "shapes", "hexagon", "compass", "star", "sparkles", "bookmark", "box", "gem"];

function parse() {
  const out = [];
  const seen = new Set();
  for (const raw of LIST.split("\n")) {
    const line = raw.trim();
    if (!line) continue;
    const at = line.indexOf(":");
    const name = line.slice(0, at).trim();
    if (seen.has(name)) throw new Error(`duplicate icon ${name}`);
    seen.add(name);
    const keywords = line.slice(at + 1).split(",").map((k) => k.trim().toLowerCase()).filter(Boolean);
    out.push({ name, keywords });
  }
  return out;
}

async function nodesOf(name) {
  const file = path.join(ICONS, `${name}.js`);
  if (!fs.existsSync(file)) return null;
  const mod = await import(pathToFileURL(file).href);
  if (!mod.__iconNode) return null;
  return mod.__iconNode.map(([tag, attrs]) => {
    const { key: _key, ...rest } = attrs;
    return [tag, rest];
  });
}

const list = parse();
const missing = [];
const glyphs = [];
for (const g of list) {
  const nodes = await nodesOf(g.name);
  if (!nodes) { missing.push(g.name); continue; }
  glyphs.push({ ...g, nodes });
}
if (missing.length) {
  console.error(`not in the installed lucide-react: ${missing.join(", ")}`);
  process.exit(1);
}
for (const n of GENERIC) if (!glyphs.some((g) => g.name === n)) throw new Error(`generic ${n} is not in the list`);
const version = JSON.parse(fs.readFileSync(path.join(TERMINAL, "node_modules/lucide-react/package.json"), "utf8")).version;
const LICENSE = "ISC. Copyright (c) Lucide Contributors 2022; portions Copyright (c) Cole Bemis 2013-2022 as part of Feather (MIT). https://lucide.dev/license";
const doc = { source: `lucide-react ${version} (ISC)`, license: LICENSE, generic: GENERIC, glyphs };

const ts = `// GENERATED by scripts/gen-workspace-glyphs.mjs from ${doc.source} — do not edit; edit the list there.
// The standard workspace icons: name, the words that pick it (a leading "~" = weak), its outline strokes.
// Icons: ${LICENSE}
export type GlyphNode = [string, Record<string, string>];
export type GlyphDef = { name: string; keywords: string[]; nodes: GlyphNode[] };
export const GLYPH_SOURCE = ${JSON.stringify(doc.source)};
export const GENERIC_GLYPHS: string[] = ${JSON.stringify(GENERIC)};
export const GLYPHS: GlyphDef[] = ${JSON.stringify(glyphs)};
`;
fs.writeFileSync(path.join(TERMINAL, "src/platform/workspaceGlyphs.data.ts"), ts);
const core = path.resolve(TERMINAL, "../../core/agent/control_plane");
if (fs.existsSync(core)) fs.writeFileSync(path.join(core, "workspace_glyphs.json"), JSON.stringify(doc) + "\n");
console.log(`${glyphs.length} glyphs written (${doc.source})`);
