# no-image-backend — `sharp`, absent on purpose

The bot reaches [`@huggingface/transformers`](https://www.npmjs.com/package/@huggingface/transformers)
through [`mixed-pipeline`](../mixed-pipeline/), which runs the pyannote **audio** segmentation model.
transformers imports [`sharp`](https://sharp.pixelplumbing.com/) at module scope for its **image**
pipeline, and sharp loads libvips, a prebuilt shared library under LGPL-3.0-or-later. FINOS lists LGPL
as Category X, and nothing in Vexa decodes an image.

`pnpm-workspace.yaml` overrides `sharp` with this package for the whole pnpm tree, so no
`@img/sharp-*` binary is installed and transformers still loads (it only checks that `sharp` is
truthy). Any call — `sharp(...)`, or one of the module functions a caller reaches for first — throws
`ImageBackendAbsent` (`code: ERR_VEXA_NO_IMAGE_BACKEND`), naming what was asked for and why there is
no backend, rather than failing somewhere less legible.

The terminal's npm project, whose images install with `npm ci` from its own lockfile, carries a copy of
`src/index.cjs` at `clients/terminal/no-image-backend` and points `sharp` at it. The parity fact
`no-image-backend-terminal` holds the two byte-identical.

`gate:image-licenses` holds both overrides in place: a libvips package back in `pnpm-lock.yaml` or in
`clients/terminal/package-lock.json` fails it.
