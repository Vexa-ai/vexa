import { log } from '../utils';

export interface AudioProcessorConfig {
  targetSampleRate: number;
  bufferSize: number;
  inputChannels: number;
  outputChannels: number;
}

export interface AudioProcessor {
  audioContext: AudioContext;
  destinationNode: MediaStreamAudioDestinationNode;
  recorder: ScriptProcessorNode;
  mediaStream: MediaStreamAudioSourceNode;
  gainNode: GainNode;
  sessionAudioStartTimeMs: number | null;
}

export class AudioService {
  private config: AudioProcessorConfig;
  private processor: AudioProcessor | null = null;

  constructor(config?: Partial<AudioProcessorConfig>) {
    this.config = {
      targetSampleRate: 16000,
      bufferSize: 4096,
      inputChannels: 1,
      outputChannels: 1,
      ...config
    };
  }

  /**
   * Find active media elements with audio tracks
   */
  async findMediaElements(retries: number = 5, delay: number = 2000): Promise<HTMLMediaElement[]> {
    for (let i = 0; i < retries; i++) {
      const mediaElements = Array.from(
        document.querySelectorAll("audio, video")
      ).filter((el: any) => 
        !el.paused && 
        el.srcObject instanceof MediaStream && 
        el.srcObject.getAudioTracks().length > 0
      ) as HTMLMediaElement[];

      if (mediaElements.length > 0) {
        log(`Found ${mediaElements.length} active media elements with audio tracks after ${i + 1} attempt(s).`);
        return mediaElements;
      }
      log(`[Audio] No active media elements found. Retrying in ${delay}ms... (Attempt ${i + 2}/${retries})`);
      await new Promise(resolve => setTimeout(resolve, delay));
    }
    return [];
  }

  /**
   * Create combined audio stream from multiple media elements
   */
  async createCombinedAudioStream(mediaElements: HTMLMediaElement[]): Promise<MediaStream> {
    if (mediaElements.length === 0) {
      throw new Error("No media elements provided for audio stream creation");
    }

    log(`Found ${mediaElements.length} active media elements.`);
    const audioContext = new AudioContext();
    const destinationNode = audioContext.createMediaStreamDestination();
    let sourcesConnected = 0;

    // Connect all media elements to the destination node
    mediaElements.forEach((element: any, index: number) => {
      try {
        const elementStream =
          element.srcObject ||
          (element.captureStream && element.captureStream()) ||
          (element.mozCaptureStream && element.mozCaptureStream());

        if (
          elementStream instanceof MediaStream &&
          elementStream.getAudioTracks().length > 0
        ) {
          const sourceNode = audioContext.createMediaStreamSource(elementStream);
          sourceNode.connect(destinationNode);
          sourcesConnected++;
          log(`Connected audio stream from element ${index + 1}/${mediaElements.length}.`);
        }
      } catch (error: any) {
        log(`Could not connect element ${index + 1}: ${error.message}`);
      }
    });

    if (sourcesConnected === 0) {
      throw new Error("Could not connect any audio streams. Check media permissions.");
    }

    log(`Successfully combined ${sourcesConnected} audio streams.`);
    return destinationNode.stream;
  }

  /**
   * Initialize audio processing pipeline
   */
  async initializeAudioProcessor(combinedStream: MediaStream): Promise<AudioProcessor> {
    const audioContext = new AudioContext();
    const destinationNode = audioContext.createMediaStreamDestination();
    const mediaStream = audioContext.createMediaStreamSource(combinedStream);
    const recorder = audioContext.createScriptProcessor(
      this.config.bufferSize,
      this.config.inputChannels,
      this.config.outputChannels
    );
    const gainNode = audioContext.createGain();
    gainNode.gain.value = 0; // Silent playback

    // Connect the audio processing pipeline
    mediaStream.connect(recorder);
    recorder.connect(gainNode);
    gainNode.connect(audioContext.destination);

    this.processor = {
      audioContext,
      destinationNode,
      recorder,
      mediaStream,
      gainNode,
      sessionAudioStartTimeMs: null
    };

    log("Audio processing pipeline connected and ready.");
    return this.processor;
  }

  /**
   * Setup audio data processing callback
   */
  setupAudioDataProcessor(
    onAudioData: (audioData: Float32Array, sessionStartTime: number | null) => void
  ): void {
    if (!this.processor) {
      throw new Error("Audio processor not initialized");
    }

    this.processor.recorder.onaudioprocess = async (event) => {
      // Set session start time on first audio chunk
      if (this.processor!.sessionAudioStartTimeMs === null) {
        this.processor!.sessionAudioStartTimeMs = Date.now();
        log(`[Audio] Session audio start time set: ${this.processor!.sessionAudioStartTimeMs}`);
      }

      const inputData = event.inputBuffer.getChannelData(0);
      const resampledData = this.resampleAudioData(inputData, this.processor!.audioContext.sampleRate);
      
      onAudioData(resampledData, this.processor!.sessionAudioStartTimeMs);
    };
  }

  /**
   * Resample audio data to target sample rate
   */
  private resampleAudioData(inputData: Float32Array, sourceSampleRate: number): Float32Array {
    const targetLength = Math.round(
      inputData.length * (this.config.targetSampleRate / sourceSampleRate)
    );
    const resampledData = new Float32Array(targetLength);
    const springFactor = (inputData.length - 1) / (targetLength - 1);
    
    resampledData[0] = inputData[0];
    resampledData[targetLength - 1] = inputData[inputData.length - 1];
    
    for (let i = 1; i < targetLength - 1; i++) {
      const index = i * springFactor;
      const leftIndex = Math.floor(index);
      const rightIndex = Math.ceil(index);
      const fraction = index - leftIndex;
      resampledData[i] =
        inputData[leftIndex] +
        (inputData[rightIndex] - inputData[leftIndex]) * fraction;
    }
    
    return resampledData;
  }

  /**
   * Get session audio start time
   */
  getSessionAudioStartTime(): number | null {
    return this.processor?.sessionAudioStartTimeMs || null;
  }

  /**
   * Set session audio start time
   */
  setSessionAudioStartTime(timeMs: number): void {
    if (this.processor) {
      this.processor.sessionAudioStartTimeMs = timeMs;
    }
  }

  /**
   * Disconnect audio processing pipeline
   */
  disconnect(): void {
    if (this.processor) {
      try {
        this.processor.recorder.disconnect();
        this.processor.mediaStream.disconnect();
        this.processor.gainNode.disconnect();
        this.processor.audioContext.close();
        log("Audio processing pipeline disconnected.");
      } catch (error: any) {
        log(`Error disconnecting audio pipeline: ${error.message}`);
      }
      this.processor = null;
    }
  }

  /**
   * Check if audio processor is initialized
   */
  isInitialized(): boolean {
    return this.processor !== null;
  }

  /**
   * Get audio context
   */
  getAudioContext(): AudioContext | null {
    return this.processor?.audioContext || null;
  }

  /**
   * Get current audio configuration
   */
  getConfig(): AudioProcessorConfig {
    return { ...this.config };
  }

  /**
   * Update audio configuration
   */
  updateConfig(newConfig: Partial<AudioProcessorConfig>): void {
    this.config = { ...this.config, ...newConfig };
  }
}
