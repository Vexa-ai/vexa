export { AudioService, type AudioProcessorConfig, type AudioProcessor, type SpeakerStreamHandle } from './audio';
export { RecordingService } from '@vexa/recording';
export { TranscriptionClient, type TranscriptionClientConfig, type TranscriptionResult } from '@vexa/speaker-streams';
export { SegmentPublisher, type SegmentPublisherConfig } from './segment-publisher';
export { SpeakerStreamManager, type SpeakerStreamManagerConfig } from '@vexa/speaker-streams';
export { resolveSpeakerName, clearSpeakerNameCache, invalidateSpeakerName, reportTrackAudio } from './speaker-identity';
