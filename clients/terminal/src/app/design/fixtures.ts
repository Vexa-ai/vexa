/** Catalogue fixtures — PLACEHOLDERS ONLY (the `/mdx-demo` rule). Generic nouns, never a person's or
 *  a company's identity: a catalogue screenshot must not be mistakable for a real record. */
export const PERSON = "Person Name";
export const COMPANY = "Example Company";
export const WORKSPACE = "Example workspace";
export const EMAIL = "name@example-domain.com";
export const URL_LONG = "https://careers.example.com/jobs/engineering/platform";
export const PATH_LONG = "example-workspace/meetings/2026-10-08-weekly-sync.md";
export const TOKEN = "vxa_example_0000000000000000";

export const PROPERTIES: Record<string, unknown> = {
  type: "company",
  website: URL_LONG,
  contact_email: EMAIL,
  founded: "2019-04-02",
  last_contact: "2026-10-09",
  tags: ["prospect", "platform"],
  aliases: ["Example Co", "Example Company Ltd", "example-company"],
  // real-shaped sources: the YAML flow list `[Gmail: 2026-09-17, …]` parses to one-key records
  sources: [
    { Gmail: "2026-09-17" },
    { "linkedin/in/person-name-0a00b0000": "2026-10-10" },
    { source: "Weekly sync notes", date: "2026-10-08", url: "https://example.com/notes/weekly-sync" },
    "Shared inbox",
  ],
  verified: false,
  address: { city: "Example City", country: "Example Country" },
  source_path: PATH_LONG,
};

export const TABS = ["Overview", "Weekly sync notes", "Pricing draft", "Security review", "Rollout plan", "Open questions"];

export const SOURCES = [
  { title: "Example Company announces platform 2026-10-08", url: "https://news.example.com/a", date: "2026-10-08", snippet: "A placeholder snippet that stands in for an article's opening lines, long enough to clamp at two lines in a narrow pane." },
  { title: "Platform documentation", url: "https://docs.example.com/platform", date: "2026-09-30" },
  { title: "Release notes", url: "https://example.com/releases", date: "2025-12-01" },
  { title: "Community thread", url: "https://forum.example.com/t/1" },
  { title: "Pricing page", url: "https://example.com/pricing" },
];

export const CHATS = ["Weekly sync", "Pricing questions", "Onboarding plan", "Security review follow-up", "Rollout checklist"];
