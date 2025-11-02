import { Page } from 'playwright';
import { log } from '../utils';

export async function ensureBrowserUtils(page: Page, scriptFsPath: string): Promise<void> {
  // 0) If already present, skip
  const already = await page.evaluate(() => !!(window as any).VexaBrowserUtils);
  if (already) return;

  // 1) Try simple addScriptTag (works when bypassCSP true)
  try {
    await page.addScriptTag({ path: scriptFsPath });
    const ok = await page.evaluate(() => !!(window as any).VexaBrowserUtils);
    if (ok) return;
  } catch (e: any) {
    log(`Warning: addScriptTag failed: ${e?.message || e}`);
  }

  // 2) Fallback: TT or Blob URL injection
  const fs = require('fs');
  try {
    const scriptContent = fs.readFileSync(scriptFsPath, 'utf8');
    await page.evaluate(async (script) => {
      const injectWithTrustedTypes = () => {
        const factory = (window as any).trustedTypes;
        if (!factory || typeof factory.createPolicy !== 'function') {
          return Promise.reject(new Error('Trusted Types not available'));
        }
        const policy = factory.createPolicy('vexaPolicy', {
          createScript: (s: string) => s,
          createScriptURL: (s: string) => s
        } as any);
        const scriptEl = document.createElement('script');
        (scriptEl as any).text = (policy as any).createScript(script);
        document.head.appendChild(scriptEl);
        return Promise.resolve();
      };

      const injectWithBlobUrl = () => new Promise<void>((resolve, reject) => {
        try {
          const blob = new Blob([script], { type: 'text/javascript' });
          const url = URL.createObjectURL(blob);
          let finalUrl: any = url;
          try {
            const factory = (window as any).trustedTypes;
            if (factory && typeof factory.createPolicy === 'function') {
              const policy = factory.createPolicy('vexaPolicy', {
                createScriptURL: (u: string) => u
              } as any);
              if (policy && typeof (policy as any).createScriptURL === 'function') {
                finalUrl = (policy as any).createScriptURL(url);
              }
            }
          } catch (_) {
            finalUrl = url; // fallback to plain blob URL
          }
          const scriptEl = document.createElement('script');
          (scriptEl as any).src = finalUrl;
          scriptEl.onload = () => resolve();
          scriptEl.onerror = () => reject(new Error('Failed to load browser utils via blob URL'));
          document.head.appendChild(scriptEl);
        } catch (err) {
          reject(err as any);
        }
      });

      return injectWithTrustedTypes().catch(() => injectWithBlobUrl());
    }, scriptContent);
  } catch (e: any) {
    log(`Error loading browser utils via evaluate: ${e?.message || e}`);
  }

  // 3) Verify
  const loaded = await page.evaluate(() => !!(window as any).VexaBrowserUtils);
  if (!loaded) {
    throw new Error('VexaBrowserUtils global is missing after injection');
  }
}


