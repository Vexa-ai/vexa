export function log(message: string): void {
  console.log(`[BotCore] ${message}`);
}

export function randomDelay(amount: number) {
  return (2 * Math.random() - 1) * (amount / 10) + amount;
}

export async function callStartupCallback(botConfig: any): Promise<void> {
  log("🔥 CALLBACK: STARTUP (active status)");
  
  if (!botConfig.botManagerCallbackUrl) {
    log("Warning: No bot manager callback URL configured. Cannot send startup callback.");
    return;
  }

  if (!botConfig.container_name) {
    log("Warning: No container name configured. Cannot send startup callback.");
    return;
  }

  if (!botConfig.connectionId) {
    log("Warning: No connection ID configured. Cannot send startup callback.");
    return;
  }

  try {
    // Extract the base URL and modify it for the startup callback
    const baseUrl = botConfig.botManagerCallbackUrl.replace('/exited', '/started');
    const startupUrl = baseUrl;
    
    const payload = {
      connection_id: botConfig.connectionId,
      container_id: botConfig.container_name
    };

    log(`Sending startup callback to ${startupUrl} with payload: ${JSON.stringify(payload)}`);

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

export async function callJoiningCallback(botConfig: any): Promise<void> {
  log("🔥 CALLBACK: JOINING (joining status)");
  
  if (!botConfig.botManagerCallbackUrl) {
    log("Warning: No bot manager callback URL configured. Cannot send joining callback.");
    return;
  }

  if (!botConfig.connectionId) {
    log("Warning: No connection ID configured. Cannot send joining callback.");
    return;
  }

  try {
    // Use the same URL structure as startup callback
    const baseUrl = botConfig.botManagerCallbackUrl.replace('/exited', '/joining');
    const joiningUrl = baseUrl;
    
    const payload = {
      connection_id: botConfig.connectionId,
      container_id: botConfig.container_name,
      status: "joining",
      timestamp: new Date().toISOString()
    };

    log(`Sending joining callback to ${joiningUrl} with payload: ${JSON.stringify(payload)}`);

    const response = await fetch(joiningUrl, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(payload)
    });

    if (response.ok) {
      log("Joining callback sent successfully");
    } else {
      log(`Joining callback failed with status: ${response.status}`);
    }
  } catch (error: any) {
    log(`Error sending joining callback: ${error.message}`);
  }
}

export async function callAwaitingAdmissionCallback(botConfig: any): Promise<void> {
  log("🔥 CALLBACK: AWAITING_ADMISSION (awaiting_admission status)");
  
  if (!botConfig.botManagerCallbackUrl) {
    log("Warning: No bot manager callback URL configured. Cannot send awaiting admission callback.");
    return;
  }

  if (!botConfig.connectionId) {
    log("Warning: No connection ID configured. Cannot send awaiting admission callback.");
    return;
  }

  try {
    // Use the same URL structure as startup callback
    const baseUrl = botConfig.botManagerCallbackUrl.replace('/exited', '/awaiting_admission');
    const awaitingAdmissionUrl = baseUrl;
    
    const payload = {
      connection_id: botConfig.connectionId,
      container_id: botConfig.container_name,
      status: "awaiting_admission",
      timestamp: new Date().toISOString()
    };

    log(`Sending awaiting admission callback to ${awaitingAdmissionUrl} with payload: ${JSON.stringify(payload)}`);

    const response = await fetch(awaitingAdmissionUrl, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(payload)
    });

    if (response.ok) {
      log("Awaiting admission callback sent successfully");
    } else {
      log(`Awaiting admission callback failed with status: ${response.status}`);
    }
  } catch (error: any) {
    log(`Error sending awaiting admission callback: ${error.message}`);
  }
}

export async function callLeaveCallback(botConfig: any, reason: string = "manual_leave"): Promise<void> {
  log("🔥 CALLBACK: LEAVE (leaving status)");
  
  if (!botConfig.botManagerCallbackUrl) {
    log("Warning: No bot manager callback URL configured. Cannot send leave callback.");
    return;
  }

  if (!botConfig.connectionId) {
    log("Warning: No connection ID configured. Cannot send leave callback.");
    return;
  }

  try {
    // Use the same URL structure as startup callback
    const baseUrl = botConfig.botManagerCallbackUrl.replace('/exited', '/leaving');
    const leavingUrl = baseUrl;
    
    const payload = {
      connection_id: botConfig.connectionId,
      container_id: botConfig.container_name,
      status: "leaving",
      reason: reason,
      timestamp: new Date().toISOString()
    };

    log(`Sending leave callback to ${leavingUrl} with payload: ${JSON.stringify(payload)}`);

    const response = await fetch(leavingUrl, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(payload)
    });

    if (response.ok) {
      log("Leave callback sent successfully");
    } else {
      log(`Leave callback failed with status: ${response.status}`);
    }
  } catch (error: any) {
    log(`Error sending leave callback: ${error.message}`);
  }
}

