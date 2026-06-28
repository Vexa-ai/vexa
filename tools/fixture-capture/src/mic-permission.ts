/**
 * One-time mic permission grant page.
 *
 * Opened in a normal tab (not the side panel / offscreen doc, which can't show
 * a permission prompt). getUserMedia here triggers Chrome's prompt; granting it
 * persists permission for the extension origin, so the offscreen capture works
 * thereafter.
 */
const statusEl = document.getElementById('status')!;

async function grant(): Promise<void> {
  statusEl.textContent = 'Requesting…';
  statusEl.className = '';
  try {
    const s = await navigator.mediaDevices.getUserMedia({ audio: true });
    s.getTracks().forEach(t => t.stop());
    statusEl.textContent = 'Microphone enabled. Go back to the Vexa panel and press Start.';
    statusEl.className = 'ok';
  } catch (err: any) {
    statusEl.textContent = `Permission ${err.name === 'NotAllowedError' ? 'denied' : 'failed'}: ${err.name}. `
      + 'Click Enable again, or allow the mic in the address bar.';
    statusEl.className = 'err';
  }
}

document.getElementById('grant')!.addEventListener('click', grant);
grant();
