/**
 * @vexa/recorder — the recorder brick (MANIFEST P5).
 *
 * A TEE on capture.v1: RawCaptureService implements CaptureV1Sink, so the host
 * composes `tee(pipelineSink, recorderSink)` — every capture.v1 message is
 * forwarded to the pipeline unchanged AND serialized to a sink (training corpus
 * via S3, or a fixture). Recording is configuration, not code change.
 */
export { RawCaptureService } from "./raw-capture";
export type { CaptureMeta } from "./raw-capture";
export { uploadCaptureToS3 } from "./s3-upload";
export * from "./contracts/capture-v1";
