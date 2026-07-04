(() => {
  const originalGetUserMedia =
    navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);

  const upstreamUrl = "http://localhost:__VEXA_STREAMER_PORT__/offer_meeting_audio";

  let peerConnection = null;
  let virtualAudioTrack = null;
  let virtualMicPromise = null;

  function showErrorOnDom(message) {
    const existing = document.getElementById("vexa-streamer-audio-error");
    if (existing) {
      existing.textContent = message;
      return;
    }

    const errorDiv = document.createElement("div");
    errorDiv.id = "vexa-streamer-audio-error";
    errorDiv.textContent = message;
    Object.assign(errorDiv.style, {
      position: "fixed",
      top: "20px",
      left: "50%",
      transform: "translateX(-50%)",
      background: "#b91c1c",
      color: "white",
      padding: "12px 20px",
      borderRadius: "6px",
      fontFamily: "system-ui, sans-serif",
      fontSize: "14px",
      zIndex: "2147483647",
      boxShadow: "0 4px 18px rgba(0,0,0,0.35)",
    });
    document.documentElement.appendChild(errorDiv);
  }

  function clearErrorOnDom() {
    const existing = document.getElementById("vexa-streamer-audio-error");
    if (existing) {
      existing.remove();
    }
  }

  function keepVirtualMicStreamAlive(stream) {
    if (!(stream instanceof MediaStream)) {
      return;
    }

    const attach = () => {
      const root = document.body || document.documentElement;
      if (!root) {
        return;
      }

      let monitor = document.getElementById("vexa-streamer-virtual-mic-monitor");
      if (!(monitor instanceof HTMLAudioElement)) {
        monitor = document.createElement("audio");
        monitor.id = "vexa-streamer-virtual-mic-monitor";
        monitor.autoplay = true;
        monitor.muted = true;
        monitor.playsInline = true;
        Object.assign(monitor.style, {
          position: "fixed",
          width: "1px",
          height: "1px",
          left: "-9999px",
          top: "-9999px",
          opacity: "0",
          pointerEvents: "none",
        });
        root.appendChild(monitor);
      }

      if (monitor.srcObject !== stream) {
        monitor.srcObject = stream;
      }
      void monitor.play?.().catch(() => undefined);
    };

    if (document.body || document.documentElement) {
      attach();
      return;
    }

    document.addEventListener("DOMContentLoaded", attach, { once: true });
  }

  async function ensureVirtualMicTrack() {
    if (virtualAudioTrack && virtualAudioTrack.readyState === "live") {
      return virtualAudioTrack;
    }
    if (virtualMicPromise) {
      return virtualMicPromise;
    }

    virtualMicPromise = (async () => {
      const deadline = Date.now() + 60000;
      let lastError = null;

      while (Date.now() < deadline) {
        const currentPeerConnection = new RTCPeerConnection();
        currentPeerConnection.addTransceiver("audio", { direction: "recvonly" });
        currentPeerConnection.addEventListener("connectionstatechange", () => {
          console.info(
            "[VexaStreamerPayload] Upstream mic peer connection state:",
            currentPeerConnection.connectionState
          );
          if (
            peerConnection === currentPeerConnection &&
            ["failed", "disconnected", "closed"].includes(currentPeerConnection.connectionState)
          ) {
            virtualAudioTrack = null;
            virtualMicPromise = null;
          }
        });

        try {
          const remoteAudioStream = await new Promise(async (resolve, reject) => {
            let resolved = false;
            const timeout = setTimeout(() => {
              if (resolved) return;
              resolved = true;
              reject(new Error("Timed out waiting for upstream meeting audio."));
            }, 12000);

            currentPeerConnection.addEventListener("track", (event) => {
              if (resolved || event.track.kind !== "audio") return;
              resolved = true;
              clearTimeout(timeout);
              const stream =
                event.streams && event.streams[0]
                  ? event.streams[0]
                  : new MediaStream([event.track]);
              resolve(stream);
            });

            const offer = await currentPeerConnection.createOffer();
            await currentPeerConnection.setLocalDescription(offer);

            const response = await fetch(upstreamUrl, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({
                sdp: currentPeerConnection.localDescription.sdp,
                type: currentPeerConnection.localDescription.type,
              }),
            });

            if (!response.ok) {
              const text = await response.text().catch(() => "");
              reject(
                new Error(
                  "Upstream meeting audio error: " + response.status + (text ? " " + text : "")
                )
              );
              return;
            }

            const answer = await response.json();
            await currentPeerConnection.setRemoteDescription(answer);
          });

          const tracks = remoteAudioStream.getAudioTracks();
          if (!tracks.length) {
            throw new Error("No meeting-audio track was received by the webpage streamer.");
          }

          const upstreamTrack = tracks[0];
          keepVirtualMicStreamAlive(remoteAudioStream);

          if (peerConnection && peerConnection !== currentPeerConnection) {
            try {
              peerConnection.close();
            } catch {}
          }

          peerConnection = currentPeerConnection;
          virtualAudioTrack = upstreamTrack;
          if (typeof upstreamTrack.addEventListener === "function") {
            upstreamTrack.addEventListener(
              "ended",
              () => {
                if (virtualAudioTrack === upstreamTrack) {
                  virtualAudioTrack = null;
                }
                if (peerConnection === currentPeerConnection) {
                  peerConnection = null;
                }
                virtualMicPromise = null;
              },
              { once: true }
            );
          }
          try {
            virtualAudioTrack.contentHint = "speech";
          } catch {}
          console.info(
            "[VexaStreamerPayload] Virtual microphone track ready:",
            JSON.stringify({
              upstreamTrackId: upstreamTrack.id,
              virtualTrackId: virtualAudioTrack.id,
              readyState: virtualAudioTrack.readyState,
              upstreamSettings:
                typeof upstreamTrack.getSettings === "function"
                  ? upstreamTrack.getSettings()
                  : null,
              settings:
                typeof virtualAudioTrack.getSettings === "function"
                  ? virtualAudioTrack.getSettings()
                  : null,
            })
          );
          clearErrorOnDom();
          return virtualAudioTrack;
        } catch (error) {
          lastError = error;
          try {
            currentPeerConnection.close();
          } catch {}
          await new Promise((resolve) => setTimeout(resolve, 1000));
        }
      }

      const errorMessage =
        lastError instanceof Error && lastError.message
          ? lastError.message
          : "Meeting audio was not received by the voice agent in time.";
      showErrorOnDom(errorMessage);
      throw new Error(errorMessage);
    })();

    try {
      return await virtualMicPromise;
    } catch (error) {
      console.error("Failed to initialize webpage-streamer virtual microphone:", error);
      virtualMicPromise = null;
      throw error;
    }
  }

  function parseConstraints(constraints) {
    let wantAudio = false;
    let wantVideo = false;
    let rawConstraints = constraints;

    if (constraints === undefined) {
      wantAudio = true;
      rawConstraints = { audio: true };
    } else if (typeof constraints === "boolean") {
      wantAudio = !!constraints;
      rawConstraints = { audio: constraints };
    } else if (typeof constraints === "object" && constraints !== null) {
      wantAudio = "audio" in constraints ? constraints.audio !== false : false;
      wantVideo = "video" in constraints ? constraints.video !== false : false;
    }

    return { wantAudio, wantVideo, rawConstraints };
  }

  navigator.mediaDevices.getUserMedia = async function interceptedGetUserMedia(constraints) {
    const { wantAudio, wantVideo, rawConstraints } = parseConstraints(constraints);

    if (!wantAudio) {
      return originalGetUserMedia(rawConstraints);
    }

    console.info(
      "[VexaStreamerPayload] Intercepting getUserMedia(audio):",
      JSON.stringify(rawConstraints)
    );
    const upstreamTrack = await ensureVirtualMicTrack();
    const outputStream = new MediaStream();
    const returnedTrack = upstreamTrack;
    try {
      returnedTrack.contentHint = "speech";
    } catch {}
    outputStream.addTrack(returnedTrack);
    keepVirtualMicStreamAlive(outputStream);
    console.info(
      "[VexaStreamerPayload] Returning virtual microphone stream:",
      JSON.stringify({
        audioTrackIds: outputStream.getAudioTracks().map((track) => track.id),
        sourceTrackId: upstreamTrack.id,
        includeVideo: !!wantVideo,
      })
    );

    if (wantVideo) {
      const videoConstraints =
        typeof rawConstraints === "object" && rawConstraints !== null
          ? { ...rawConstraints, audio: false, video: rawConstraints.video || true }
          : { audio: false, video: true };
      const realVideoStream = await originalGetUserMedia(videoConstraints);
      for (const track of realVideoStream.getVideoTracks()) {
        outputStream.addTrack(track);
      }
    }

    return outputStream;
  };
})();
