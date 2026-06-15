export { AudioService, type AudioProcessorConfig, type AudioProcessor, type SpeakerStreamHandle } from './audio';
export { RecordingService } from '@vexa/recording';
export { TranscriptionClient, type TranscriptionClientConfig, type TranscriptionResult } from '@vexa/pipeline';
export { SegmentPublisher, type SegmentPublisherConfig } from './segment-publisher';
export { SpeakerStreamManager, type SpeakerStreamManagerConfig } from '@vexa/pipeline';
export { resolveSpeakerName, clearSpeakerNameCache, invalidateSpeakerName, reportTrackAudio } from './speaker-identity';
