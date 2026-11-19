# vendor/mediapipe

The segmentation model the call's background blur runs (`selfie_segmenter.tflite`, MediaPipe's
selfie segmenter, float16, Apache-2.0). `scripts/copy-mediapipe.mjs` copies it, with its licence
(`selfie_segmenter.LICENSE.txt`), to `public/mediapipe/`, where the terminal serves it — the app
never fetches it from a CDN.

| | |
| --- | --- |
| Source | `https://storage.googleapis.com/mediapipe-models/image_segmenter/selfie_segmenter/float16/1/selfie_segmenter.tflite` (version `1`, identical to `latest` on 2026-10-16) |
| sha256 | `191ac9529ae506ee0beefa6b2c945a172dab9d07d1e802a290a4e4038226658b` (249,537 bytes) |
| Licence | Apache-2.0 — the model card ("Model Card MediaPipe Selfie Segmentation", Google, 2021-05-06) states it; the text is MediaPipe's `LICENSE` at `v0.10.14`, the `@mediapipe/tasks-vision` version the runtime uses |

Without it here, `npm run build` fails: a build without the model ships a blur that can never start.
The test `src/minutes/__tests__/blurAssets.test.ts` pins the hash.
