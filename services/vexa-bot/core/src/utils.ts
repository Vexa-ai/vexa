export function log(message: string): void {
  console.log(`[BotCore] ${message}`);
}

export function randomDelay(amount: number) {
  return (2 * Math.random() - 1) * (amount / 10) + amount;
}

export async function callStartupCallback(botConfig: any): Promise<void> {
  if (!botConfig.botManagerCallbackUrl) {
    log("Warning: No bot manager callback URL configured. Cannot send startup callback.");
    return;
  }

  if (!botConfig.container_name) {
    log("Warning: No container name configured. Cannot send startup callback.");
    return;
  }

  try {
    // Extract the base URL and modify it for the startup callback
    const baseUrl = botConfig.botManagerCallbackUrl.replace('/exited', '/started');
    const startupUrl = baseUrl;
    
    const payload = {
      container_name: botConfig.container_name,
      status: "started",
      timestamp: new Date().toISOString(),
      platform: botConfig.platform,
      meeting_id: botConfig.nativeMeetingId,
      meeting_url: botConfig.meetingUrl
    };

    const response = await fetch(startupUrl, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(payload)
    });

    if (response.ok) {
      log("Startup callback sent successfully");
    } else {
      log(`Startup callback failed with status: ${response.status}`);
    }
  } catch (error: any) {
    log(`Error sending startup callback: ${error.message}`);
  }
}

