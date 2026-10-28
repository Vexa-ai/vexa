# ui-kit/format — display formatting

**Concern.** One formatter per kind of value, so the same value reads the same everywhere
(terminal design guidelines §5.3).

**Surface.** `date.ts` — `formatDate` (relative within 7 days, absolute beyond, the year only when
it differs; a date-only value is a calendar day in every time zone), `fullDate` (the tooltip),
`isoDate` (`<time dateTime>`), `parseDate`, `looksLikeDate`. Pure: `now`, `locale` and `timeZone`
are parameters. Rendered through the `DateText` primitive.

`contrast.ts` — WCAG 2.2 contrast (`contrastRatio`, alpha colours blended over the surface), used by
the contrast gate and the catalogue's live ratios. `tokens.ts` — `parseTokens(css)`: the token
stylesheet (`src/app/tokens.css`) as a resolved name → value map per theme.

**Dependencies.** `Intl.DateTimeFormat` only.
