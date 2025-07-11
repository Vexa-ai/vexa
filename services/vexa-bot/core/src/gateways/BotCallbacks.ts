export type TranscriptionSegment = {
  start: number;
  end: number;
  text: string;
  speaker: string;
  created_at: string;
  language: string;
  absolute_start_time: string;
  absolute_end_time: string;
};

export interface BotCallbacks {
  onMeetingEnd: (botConnectionId: string) => Promise<void>;
  onTranscriptionSegmentsReceived: (
    data: TranscriptionSegment[] | TranscriptionSegment
  ) => void;
  onStartRecording: (
    videoFilePath: string,
    botConnectionId: string
  ) => Promise<void>;
}
