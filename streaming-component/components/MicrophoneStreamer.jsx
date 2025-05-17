import React, { useState, useRef, useEffect, useCallback } from "react";

// --- Configuration ---
const WEBSOCKET_URL = "ws://localhost:9092"; // Use ws:// or wss:// as appropriate
const TARGET_SAMPLE_RATE = 16000;
const SCRIPT_PROCESSOR_BUFFER_SIZE = 4096; // Adjust buffer size as needed (powers of 2)

// --- Helper Functions ---
/**
 * Generates a simple UUID for client identification.
 */
function generateUUID() {
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, function (c) {
    const r = (Math.random() * 16) | 0,
      v = c === "x" ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

/**
 * Resamples an audio buffer from sourceRate to targetRate using linear interpolation.
 * @param {AudioBuffer} audioBuffer The input AudioBuffer.
 * @param {number} targetSampleRate The desired sample rate (e.g., 16000).
 * @returns {Float32Array} The resampled audio data as a Float32Array.
 */
function resampleBuffer(audioBuffer, targetSampleRate) {
  const sourceData = audioBuffer.getChannelData(0); // Assuming mono
  const sourceSampleRate = audioBuffer.sampleRate;

  if (sourceSampleRate === targetSampleRate) {
    return sourceData;
  }

  const sourceLength = sourceData.length;
  const targetLength = Math.round(
    sourceLength * (targetSampleRate / sourceSampleRate)
  );
  const resampledData = new Float32Array(targetLength);
  const ratio = (sourceLength - 1) / (targetLength - 1);

  resampledData[0] = sourceData[0];
  resampledData[targetLength - 1] = sourceData[sourceLength - 1];

  for (let i = 1; i < targetLength - 1; i++) {
    const index = i * ratio;
    const indexPrev = Math.floor(index);
    const indexNext = Math.min(indexPrev + 1, sourceLength - 1); // Ensure within bounds
    const fraction = index - indexPrev;

    resampledData[i] =
      sourceData[indexPrev] +
      (sourceData[indexNext] - sourceData[indexPrev]) * fraction;
  }

  return resampledData;
}

// --- React Component ---
function MicrophoneStreamer() {
  const [isRecording, setIsRecording] = useState(false);
  const [status, setStatus] = useState("Idle");
  const [error, setError] = useState(null);
  const [transcript, setTranscript] = useState([]); // Store transcript segments

  // Refs for non-state instances
  const socketRef = useRef(null);
  const audioContextRef = useRef(null);
  const mediaStreamRef = useRef(null);
  const processorNodeRef = useRef(null);
  const sourceNodeRef = useRef(null);
  const clientUidRef = useRef(generateUUID()); // Generate UID once per component instance

  const stopRecording = useCallback(
    (finalStatus = "Stopped") => {
      if (
        !isRecording &&
        !mediaStreamRef.current &&
        !audioContextRef.current &&
        !socketRef.current
      ) {
        console.log("Stop Recording: Already stopped or not started.");
        return; // Avoid redundant stops
      }
      console.log(`Stop Recording: Setting status to ${finalStatus}`);
      setStatus(finalStatus);
      setIsRecording(false);

      // 1. Stop WebSocket
      if (socketRef.current) {
        if (socketRef.current.readyState === WebSocket.OPEN) {
          try {
            // Optional: Send end signal if required by the server protocol
            // socketRef.current.send("END_OF_AUDIO");
            console.log("Stop Recording: Closing WebSocket.");
            socketRef.current.close();
          } catch (err) {
            console.error("Error closing WebSocket:", err);
          }
        }
        socketRef.current = null; // Clear ref
      }

      // 2. Stop Audio Processing & Microphone
      if (processorNodeRef.current) {
        console.log("Stop Recording: Disconnecting processor node.");
        processorNodeRef.current.disconnect();
        processorNodeRef.current.onaudioprocess = null; // Remove handler
        processorNodeRef.current = null;
      }
      if (sourceNodeRef.current) {
        console.log("Stop Recording: Disconnecting source node.");
        sourceNodeRef.current.disconnect();
        sourceNodeRef.current = null;
      }
      if (mediaStreamRef.current) {
        console.log("Stop Recording: Stopping media stream tracks.");
        mediaStreamRef.current.getTracks().forEach((track) => track.stop());
        mediaStreamRef.current = null;
      }
      if (audioContextRef.current) {
        // Close AudioContext only if it's running/suspended, not already closed
        if (audioContextRef.current.state !== "closed") {
          console.log("Stop Recording: Closing AudioContext.");
          audioContextRef.current
            .close()
            .catch((err) => console.error("Error closing AudioContext:", err));
        }
        audioContextRef.current = null;
      }
    },
    [isRecording]
  ); // Include isRecording dependency

  const handleStartRecording = async () => {
    if (isRecording) return;
    setError(null);
    setTranscript([]); // Clear previous transcript
    setStatus("Requesting Mic...");

    try {
      // 1. Get Microphone Access
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: true,
        video: false,
      });
      mediaStreamRef.current = stream;
      setStatus("Mic Access Granted");

      // 2. Set up Web Audio API
      const context = new (window.AudioContext || window.webkitAudioContext)();
      audioContextRef.current = context;

      // Check if sample rate matches target, warn if not (resampling will handle it)
      if (context.sampleRate !== TARGET_SAMPLE_RATE) {
        console.warn(
          `AudioContext sample rate is ${context.sampleRate}Hz, resampling to ${TARGET_SAMPLE_RATE}Hz.`
        );
      }

      const source = context.createMediaStreamSource(stream);
      sourceNodeRef.current = source;

      // Using ScriptProcessorNode (deprecated but simpler for example)
      const processor = context.createScriptProcessor(
        SCRIPT_PROCESSOR_BUFFER_SIZE,
        1, // Input channels (mono)
        1 // Output channels (mono) - required even if not using output
      );
      processorNodeRef.current = processor;

      setStatus("Connecting WS...");

      // 3. Set up WebSocket
      const ws = new WebSocket(WEBSOCKET_URL);
      socketRef.current = ws;

      ws.onopen = () => {
        console.log("WebSocket connection opened.");
        setStatus("WS Connected, Sending Config...");

        // Send initial configuration message
        const configMessage = {
          uid: clientUidRef.current,
          language: null, // Let backend detect language
          task: "transcribe",
          model: "medium", // Example model size
          use_vad: true, // Example VAD setting
          // Add other fields if your specific server requires them (platform, token etc.)
          // platform: "web_mic",
          // token: "your_dummy_token",
          // meeting_id: "mic_stream_" + clientUidRef.current,
          // meeting_url: null
        };
        try {
          ws.send(JSON.stringify(configMessage));
          console.log("Sent WS config:", configMessage);

          // Setup audio processing ONLY after config is sent
          processor.onaudioprocess = (event) => {
            if (
              !isRecording ||
              !socketRef.current ||
              socketRef.current.readyState !== WebSocket.OPEN
            ) {
              return; // Stop processing if not recording or socket closed
            }
            try {
              const inputBuffer = event.inputBuffer;
              const resampledData = resampleBuffer(
                inputBuffer,
                TARGET_SAMPLE_RATE
              );

              // Send the resampled audio data as binary
              socketRef.current.send(resampledData.buffer);
            } catch (processErr) {
              console.error("Error in onaudioprocess:", processErr);
              setError(`Audio processing error: ${processErr.message}`);
              stopRecording("Error"); // Stop recording on processing error
            }
          };

          // Connect the audio graph: Source -> Processor -> Destination (needed for processor to run)
          source.connect(processor);
          processor.connect(context.destination);

          // Update state: Now actually recording
          setStatus("Recording...");
          setIsRecording(true);
        } catch (sendError) {
          console.error("Error sending WS config:", sendError);
          setError(`WebSocket send error: ${sendError.message}`);
          setStatus("Error");
          ws.close(); // Close socket if config send fails
        }
      };

      ws.onmessage = (event) => {
        try {
          const messageData = JSON.parse(event.data);
          console.log("WS message received:", messageData);

          // Handle server readiness or other status messages
          if (messageData.message === "SERVER_READY") {
            console.log("Server is ready.");
            // Potentially update status if needed, but already 'Recording'
            return;
          }
          if (messageData.status === "ERROR") {
            console.error("Server Error:", messageData.message);
            setError(`Server error: ${messageData.message}`);
            stopRecording("Error");
            return;
          }

          // Append transcript segments (adjust based on actual server response structure)
          if (messageData.segments && Array.isArray(messageData.segments)) {
            // Simple example: just collect the text
            const newTexts = messageData.segments
              .map((seg) => seg.text || "")
              .filter((text) => text.trim() !== "");
            if (newTexts.length > 0) {
              setTranscript((prev) => [...prev, ...newTexts]);
            }
          }
        } catch (parseError) {
          console.error(
            "Error parsing WebSocket message:",
            parseError,
            "Data:",
            event.data
          );
        }
      };

      ws.onerror = (err) => {
        console.error("WebSocket error:", err);
        // Attempt to get more specific error if possible
        const errorMessage = err.message || "WebSocket connection error";
        setError(`WebSocket error: ${errorMessage}`);
        stopRecording("Error");
      };

      ws.onclose = (event) => {
        console.log(
          `WebSocket closed: Code=${event.code}, Reason=${event.reason}`
        );
        // Only update status if it wasn't an intentional stop or an error stop
        if (status !== "Stopped" && status !== "Error") {
          stopRecording("Disconnected"); // Use a different status for unexpected close
        }
      };
    } catch (err) {
      console.error("Error starting recording:", err);
      setError(`Error: ${err.message}`);
      setStatus("Error");
      // Ensure cleanup happens if partially started
      stopRecording("Error");
    }
  };

  // Effect for cleanup on unmount
  useEffect(() => {
    // Return the cleanup function
    return () => {
      console.log("Component unmounting, ensuring cleanup.");
      stopRecording("Unmounted");
    };
  }, [stopRecording]); // Add stopRecording as dependency

  return (
    <div>
      <h2>Microphone Streamer</h2>
      <p>Status: {status}</p>
      <div>
        <button onClick={handleStartRecording} disabled={isRecording}>
          Start Recording
        </button>
        <button onClick={() => stopRecording()} disabled={!isRecording}>
          Stop Recording
        </button>
      </div>
      {error && <p style={{ color: "red" }}>Error: {error}</p>}
      <div>
        <h3>Live Transcript (Simple):</h3>
        <div
          style={{
            height: "200px",
            overflowY: "scroll",
            border: "1px solid #ccc",
            padding: "5px",
            marginTop: "10px",
          }}
        >
          {transcript.map((text, index) => (
            <p key={index}>{text}</p>
          ))}
        </div>
      </div>
    </div>
  );
}

export default MicrophoneStreamer;
