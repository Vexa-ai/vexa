import { context } from 'esbuild';
import { cpSync, mkdirSync, rmSync, writeFileSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const WATCH = process.argv.includes('--watch');

// Consume the v0.12 capture bricks by PACKAGE NAME (their published surface), not
// by reaching into their src/. Aliased to each brick's index so esbuild still
// bundles the TS source in. gmeet (per-channel) + mixed-capture-core (the mixed
// lane — YouTube, Zoom, and Teams via zoom-capture/teams-capture).
const vexaAlias = {
  '@vexa/gmeet-capture': resolve(__dirname, '../../meetings/modules/gmeet-capture/src/index.ts'),
  '@vexa/capture-codec': resolve(__dirname, '../../meetings/modules/capture-codec/src/index.ts'),
  '@vexa/mixed-capture-core': resolve(__dirname, '../../meetings/modules/mixed-capture-core/src/index.ts'),
  '@vexa/zoom-capture': resolve(__dirname, '../../meetings/modules/zoom-capture/src/index.ts'),
  '@vexa/teams-capture': resolve(__dirname, '../../meetings/modules/teams-capture/src/index.ts'),
  '@vexa/record-chunker': resolve(__dirname, '../../meetings/modules/record-chunker/src/index.ts'),
};

const outdir = 'dist';
rmSync(outdir, { recursive: true, force: true });
mkdirSync(outdir, { recursive: true });

const pad = (n) => String(n).padStart(2, '0');

// After EVERY (re)build: copy static assets + version the manifest. The version
// bump rewrites build-stamp.txt, which the loaded extension watches → auto-reload.
const postBuild = {
  name: 'post-build',
  setup(b) {
    b.onEnd((result) => {
      if (result.errors.length) return;
      cpSync('src/sidepanel.html', `${outdir}/sidepanel.html`);
      cpSync('src/offscreen.html', `${outdir}/offscreen.html`);
      cpSync('src/mic-permission.html', `${outdir}/mic-permission.html`);
      cpSync('assets', `${outdir}/assets`, { recursive: true });
      const now = new Date();
      const stampHuman = `${pad(now.getMonth() + 1)}-${pad(now.getDate())} ${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`;
      writeFileSync(`${outdir}/build-stamp.txt`, JSON.stringify({ ts: Date.now(), human: stampHuman }));
      const manifest = JSON.parse(readFileSync('manifest.json', 'utf8'));
      manifest.version = `0.${now.getMonth() + 1}${pad(now.getDate())}.${now.getHours()}${pad(now.getMinutes())}.${now.getSeconds()}`;
      manifest.version_name = `dev ${stampHuman}`;
      writeFileSync(`${outdir}/manifest.json`, JSON.stringify(manifest, null, 2));
      console.log(`Built vexa-extension → dist/  (manifest ${manifest.version}${WATCH ? ', watching…' : ''})`);
    });
  },
};

const ctx = await context({
  entryPoints: {
    background: 'src/background.ts',
    content: 'src/content.ts',
    inpage: 'src/inpage.ts',
    sidepanel: 'src/sidepanel.ts',
    offscreen: 'src/offscreen.ts',
    'mic-permission': 'src/mic-permission.ts',
  },
  outdir,
  bundle: true,
  format: 'iife',
  target: 'es2020',
  alias: vexaAlias,
  logLevel: 'silent',
  plugins: [postBuild],
});

if (WATCH) {
  await ctx.watch();           // rebuilds on any src/ or aliased-brick change → extension auto-reloads
} else {
  await ctx.rebuild();
  await ctx.dispose();
}
