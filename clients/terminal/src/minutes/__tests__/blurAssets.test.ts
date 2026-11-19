// @vitest-environment node
/** The background blur's assets are served by this app, never fetched from Google (2026-10-16: the
 *  model was missing from vendor/mediapipe/, so every build shipped a blur that could not start —
 *  "Background blur couldn't start here"). The model resolves to a same-origin path, the pinned model
 *  is in the tree, and the build step puts it, its licence and the wasm runtime where that path points. */
import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { existsSync, readFileSync, readdirSync } from "node:fs";
import { basename, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { BLUR_ASSETS } from "../callBlur";

const root = fileURLToPath(new URL("../../../", import.meta.url));
const MODEL_SHA256 = "191ac9529ae506ee0beefa6b2c945a172dab9d07d1e802a290a4e4038226658b";
const sha256 = (p: string) => createHash("sha256").update(readFileSync(p)).digest("hex");

describe("background blur assets", () => {
  it("resolves the runtime and the model from this app's own /mediapipe/, not a CDN", () => {
    expect(BLUR_ASSETS.modelAssetPath).toBe("/mediapipe/selfie_segmenter.tflite");
    expect(BLUR_ASSETS.tasksVisionFileSet).toBe("/mediapipe/wasm");
    for (const p of Object.values(BLUR_ASSETS)) expect(p).not.toMatch(/^[a-z]+:|^\/\//i);
  });

  it("vendors the pinned Apache-2.0 model with its licence beside it", () => {
    const model = join(root, "vendor", "mediapipe", basename(BLUR_ASSETS.modelAssetPath));
    expect(existsSync(model)).toBe(true);
    expect(sha256(model)).toBe(MODEL_SHA256);
    const licence = readFileSync(join(root, "vendor", "mediapipe", "selfie_segmenter.LICENSE.txt"), "utf8");
    expect(licence).toContain("Apache License");
    expect(licence).toContain("Version 2.0, January 2004");
  });

  it("the build step serves the model, its licence and the wasm runtime at the paths the blur loads", () => {
    execFileSync(process.execPath, [join(root, "scripts", "copy-mediapipe.mjs")], { cwd: root, stdio: "pipe" });
    const pub = join(root, "public");
    const model = join(pub, BLUR_ASSETS.modelAssetPath);
    expect(existsSync(model)).toBe(true);
    expect(sha256(model)).toBe(MODEL_SHA256);
    expect(existsSync(join(pub, "mediapipe", "selfie_segmenter.LICENSE.txt"))).toBe(true);
    const wasm = readdirSync(join(pub, BLUR_ASSETS.tasksVisionFileSet));
    expect(wasm.some((f) => f.endsWith(".wasm"))).toBe(true);
    expect(wasm.some((f) => f.endsWith(".js"))).toBe(true);
  });
});
