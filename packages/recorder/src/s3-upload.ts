import { execSync } from 'child_process';

/**
 * Upload a finalized raw-capture dir to the telemetry bucket under a
 * PARTITIONED prefix so captures are selectable by platform / date / meeting
 * with no database — the S3 prefix IS the index:
 *   telemetry/capture/v1/platform=<p>/date=<YYYY-MM-DD>/<meetingId>/
 * meta.json (written by RawCaptureService.finalize) carries num_speakers,
 * language, speakers[], etc. for finer selection (s3 select / listing + jq).
 * Best-effort: never throws into the shutdown path.
 */
export function uploadCaptureToS3(
  localDir: string,
  opts: { platform?: string; meetingId: string | number; bucket?: string; endpoint?: string; accessKey?: string; secretKey?: string },
): void {
  try {
    const bucket = opts.bucket || process.env.TELEMETRY_S3_BUCKET;
    const endpoint = opts.endpoint || process.env.TELEMETRY_S3_ENDPOINT || process.env.S3_ENDPOINT;
    if (!bucket) { console.log('[telemetry] TELEMETRY_S3_BUCKET unset — capture stays local'); return; }
    const platform = (opts.platform || 'unknown').replace(/[^a-z0-9_]+/gi, '-');
    const date = new Date().toISOString().slice(0, 10);
    const prefix = `telemetry/capture/v1/platform=${platform}/date=${date}/${opts.meetingId}`;
    const env = {
      ...process.env as Record<string, string>,
      AWS_ACCESS_KEY_ID: opts.accessKey || process.env.TELEMETRY_S3_ACCESS_KEY || process.env.AWS_ACCESS_KEY_ID || '',
      AWS_SECRET_ACCESS_KEY: opts.secretKey || process.env.TELEMETRY_S3_SECRET_KEY || process.env.AWS_SECRET_ACCESS_KEY || '',
    };
    const ep = endpoint ? `--endpoint-url "${endpoint}"` : '';
    execSync(`aws s3 sync "${localDir}/" "s3://${bucket}/${prefix}/" ${ep}`,
      { env, stdio: 'pipe', timeout: 300000 });
    console.log(`[telemetry] capture uploaded → s3://${bucket}/${prefix}/`);
  } catch (err: any) {
    console.log(`[telemetry] capture upload failed (non-fatal): ${err.message}`);
  }
}
