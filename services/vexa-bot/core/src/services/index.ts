export { AudioService, type AudioProcessorConfig, type AudioProcessor, type SpeakerStreamHandle } from './audio';
export { RecordingService } from './recording';
export { TranscriptionClient, type TranscriptionClientConfig, type TranscriptionResult } from './transcription-client';
export { SegmentPublisher, type SegmentPublisherConfig } from './segment-publisher';
export { SpeakerStreamManager, type SpeakerStreamManagerConfig } from './speaker-streams';
export { resolveSpeakerName, clearSpeakerNameCache, invalidateSpeakerName } from './speaker-identity';
