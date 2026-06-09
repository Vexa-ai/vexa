import { build } from 'esbuild';
import { cpSync, mkdirSync, rmSync } from 'node:fs';

const outdir = 'dist';
rmSync(outdir, { recursive: true, force: true });
mkdirSync(outdir, { recursive: true });

await build({
  entryPoints: {
    background: 'src/background.ts',
    content: 'src/content.ts',
    inpage: 'src/inpage.ts',
    sidepanel: 'src/sidepanel.ts',
  },
  outdir,
  bundle: true,
  format: 'iife',
  target: 'es2020',
  logLevel: 'info',
});

cpSync('manifest.json', `${outdir}/manifest.json`);
cpSync('src/sidepanel.html', `${outdir}/sidepanel.html`);
cpSync('assets', `${outdir}/assets`, { recursive: true });

console.log('Built vexa-extension → dist/. Load that folder as an unpacked extension.');
