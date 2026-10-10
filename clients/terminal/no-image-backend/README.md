# no-image-backend — the terminal's `sharp`, absent on purpose

`next` lists `sharp` as an optional dependency for its image optimizer, and `sharp` installs libvips, a
prebuilt library under LGPL-3.0-or-later, which FINOS lists as Category X. The terminal turns the
optimizer off (`next.config.ts` `images.unoptimized`), so it needs no image backend.

The terminal is its own npm project: its images run `npm ci` from `../package-lock.json` with
`clients/terminal` as the whole build context, so it cannot link the pnpm workspace's stand-in at
`core/meetings/modules/no-image-backend`. This directory is a copy of it. `../package.json` depends on
it as `sharp` and overrides `next`'s `sharp` to the same spec (`"$sharp"`), so no `@img/*` package is
locked or installed in any stage. Any call throws `ImageBackendAbsent`
(`code: ERR_VEXA_NO_IMAGE_BACKEND`).

`src/index.cjs` is held byte-identical to the module's by the parity fact `no-image-backend-terminal`
(`scripts/parity.json`); edit the module's copy and vendor it here. `gate:image-licenses` fails if
`../package-lock.json` locks an `@img/sharp-*` package or stops resolving `sharp` here.
