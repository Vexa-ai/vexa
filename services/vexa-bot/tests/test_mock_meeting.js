/**
 * Test per-speaker audio capture against mock meeting page.
 *
 * Uses existing Playwright CDP browser at localhost:9222.
 * Opens mock meeting page, discovers audio elements, verifies
 * separate streams can be captured.
 *
 * Prerequisites:
 *   - Mock meeting serving: cd tests/mock-meeting && python3 -m http.server 8089
 *   - Browser with CDP: docker (playwright-vnc-poc containers)
 *
 * Run:
 *   node tests/test_mock_meeting.js
 */

const { chromium } = require('/home/dima/dev/playwright-vnc-poc/node_modules/playwright');

(async () => {
  console.log('=== Mock Meeting Per-Speaker Test ===\n');

  const browser = await chromium.connectOverCDP('http://localhost:9222');
  const context = browser.contexts()[0];
  const page = await context.newPage();

  try {
    // 1. Open mock meeting
    console.log('1. Opening mock meeting page...');
    // Browser runs inside Docker — use host.docker.internal to reach host
    const mockUrl = process.env.MOCK_MEETING_URL || 'http://host.docker.internal:8089';
    await page.goto(mockUrl);
    await page.waitForTimeout(2000);

    // 2. Start playing all audio (bypass autoplay policy)
    console.log('2. Starting audio playback...');
    await page.evaluate(() => {
      document.querySelectorAll('audio').forEach(a => {
        a.loop = true;
        a.play().catch(() => {});
      });
    });
    await page.waitForTimeout(1000);

    // 3. Discover audio elements
    console.log('3. Discovering audio elements...');
    const audioInfo = await page.evaluate(() => {
      const elements = Array.from(document.querySelectorAll('audio'));
      return elements.map((el, i) => ({
        index: i,
        id: el.id,
        src: el.src,
        paused: el.paused,
        hasStream: el.srcObject instanceof MediaStream,
        duration: el.duration,
      }));
    });

    console.log(`   Found ${audioInfo.length} audio elements:`);
    for (const a of audioInfo) {
      console.log(`   [${a.index}] id=${a.id} paused=${a.paused} duration=${a.duration.toFixed(1)}s`);
    }

    // 3. Resolve speaker names from DOM
    console.log('\n3. Resolving speaker names from DOM...');
    const speakerNames = await page.evaluate(() => {
      const elements = Array.from(document.querySelectorAll('audio'));
      return elements.map((el) => {
        // Walk up to find participant container
        const container = el.closest('.participant');
        if (container) {
          const nameEl = container.querySelector('.participant-name');
          if (nameEl) return nameEl.textContent.trim();
        }
        return 'Unknown';
      });
    });

    for (let i = 0; i < speakerNames.length; i++) {
      console.log(`   [${i}] ${speakerNames[i]}`);
    }

    // 4. Create per-speaker audio capture (browser-side)
    console.log('\n4. Creating per-speaker audio streams...');
    const captureResult = await page.evaluate(async () => {
      const elements = Array.from(document.querySelectorAll('audio')).filter(
        el => !el.paused
      );

      const results = [];

      for (let i = 0; i < elements.length; i++) {
        const el = elements[i];
        try {
          // Get the element's media stream
          const stream = el.captureStream ? el.captureStream() : el.mozCaptureStream();

          if (!(stream instanceof MediaStream) || stream.getAudioTracks().length === 0) {
            results.push({ index: i, error: 'No audio tracks' });
            continue;
          }

          // Create separate AudioContext for this speaker
          const audioContext = new AudioContext({ sampleRate: 16000 });
          const source = audioContext.createMediaStreamSource(stream);
          const processor = audioContext.createScriptProcessor(4096, 1, 1);

          let chunkCount = 0;
          let totalSamples = 0;

          processor.onaudioprocess = (e) => {
            const data = e.inputBuffer.getChannelData(0);
            const hasAudio = data.some(s => Math.abs(s) > 0.01);
            if (hasAudio) {
              chunkCount++;
              totalSamples += data.length;
            }
          };

          source.connect(processor);
          processor.connect(audioContext.destination);

          // Capture for 3 seconds
          await new Promise(r => setTimeout(r, 3000));

          processor.disconnect();
          source.disconnect();
          await audioContext.close();

          results.push({
            index: i,
            chunkCount,
            totalSamples,
            durationCaptured: (totalSamples / 16000).toFixed(2) + 's',
            hasAudio: chunkCount > 0,
          });
        } catch (err) {
          results.push({ index: i, error: err.message });
        }
      }

      return results;
    });

    console.log('   Per-speaker capture results:');
    let allPassed = true;
    for (const r of captureResult) {
      const name = speakerNames[r.index] || 'Unknown';
      if (r.error) {
        console.log(`   [${r.index}] ${name}: FAIL — ${r.error}`);
        allPassed = false;
      } else if (r.hasAudio) {
        console.log(`   [${r.index}] ${name}: PASS — ${r.chunkCount} chunks, ${r.durationCaptured} captured`);
      } else {
        console.log(`   [${r.index}] ${name}: FAIL — no audio detected`);
        allPassed = false;
      }
    }

    // 5. Verify separation
    console.log('\n5. Verification:');
    console.log(`   Audio elements found: ${audioInfo.length}`);
    console.log(`   Speakers resolved: ${speakerNames.filter(n => n !== 'Unknown').length}`);
    console.log(`   Streams with audio: ${captureResult.filter(r => r.hasAudio).length}`);
    console.log(`   All passed: ${allPassed}`);

    if (allPassed && captureResult.length === 3) {
      console.log('\n   ✓ Per-speaker audio separation works. 3 independent streams captured.');
    } else {
      console.log('\n   ✗ Issues detected — check results above.');
    }

  } catch (err) {
    console.error('Error:', err.message);
  } finally {
    await page.close();
  }
})().catch(console.error);
