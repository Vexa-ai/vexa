// src/timestamps.ts
function parseUTCTimestamp(timestamp) {
  const hasZone = /[zZ]$/.test(timestamp) || /[+-]\d{2}:\d{2}$/.test(timestamp);
  return new Date(hasZone ? timestamp : `${timestamp}Z`);
}

// src/dedup.ts
function normalizeText(t) {
  return (t || "").trim().toLowerCase().replace(/[.,!?;:]+$/g, "").replace(/\s+/g, " ");
}
function deduplicateSegments(segments) {
  if (segments.length === 0) return segments;
  const deduped = [];
  for (const seg of segments) {
    if (deduped.length === 0) {
      deduped.push(seg);
      continue;
    }
    const last = deduped[deduped.length - 1];
    if ((seg.speaker || "") !== (last.speaker || "")) {
      deduped.push(seg);
      continue;
    }
    const segStart = parseUTCTimestamp(seg.absolute_start_time).getTime();
    const segEnd = parseUTCTimestamp(seg.absolute_end_time).getTime();
    const lastStart = parseUTCTimestamp(last.absolute_start_time).getTime();
    const lastEnd = parseUTCTimestamp(last.absolute_end_time).getTime();
    const segStartSec = segStart / 1e3;
    const segEndSec = segEnd / 1e3;
    const lastStartSec = lastStart / 1e3;
    const lastEndSec = lastEnd / 1e3;
    const sameText = (seg.text || "").trim() === (last.text || "").trim();
    const overlaps = Math.max(segStartSec, lastStartSec) < Math.min(segEndSec, lastEndSec);
    const gapSec = (segStart - lastEnd) / 1e3;
    if (!overlaps && sameText && gapSec >= 0 && gapSec <= 1) {
      if (preferSeg(seg, last)) {
        deduped[deduped.length - 1] = seg;
      }
      continue;
    }
    if (overlaps) {
      const segFullyInsideLast = segStartSec >= lastStartSec && segEndSec <= lastEndSec;
      const lastFullyInsideSeg = lastStartSec >= segStartSec && lastEndSec <= segEndSec;
      if (sameText) {
        if (preferSeg(seg, last)) {
          deduped[deduped.length - 1] = seg;
        }
        continue;
      }
      if (segFullyInsideLast) continue;
      if (lastFullyInsideSeg) {
        deduped[deduped.length - 1] = seg;
        continue;
      }
      const segTextClean = normalizeText(seg.text || "");
      const lastTextClean = normalizeText(last.text || "");
      const segDuration = segEndSec - segStartSec;
      const lastDuration = lastEndSec - lastStartSec;
      const overlapStart = Math.max(segStartSec, lastStartSec);
      const overlapEnd = Math.min(segEndSec, lastEndSec);
      const overlapDuration = overlapEnd - overlapStart;
      const overlapRatioSeg = segDuration > 0 ? overlapDuration / segDuration : 0;
      const overlapRatioLast = lastDuration > 0 ? overlapDuration / lastDuration : 0;
      const segExpandsLast = Boolean(lastTextClean) && Boolean(segTextClean) && segTextClean.includes(lastTextClean) && segTextClean.length > lastTextClean.length;
      if (segExpandsLast && overlapRatioLast >= 0.5 && (seg.completed || !last.completed)) {
        deduped[deduped.length - 1] = seg;
        continue;
      }
      const segIsTailRepeat = Boolean(segTextClean) && Boolean(lastTextClean) && lastTextClean.includes(segTextClean);
      if (segIsTailRepeat) {
        const segWordCount = segTextClean.split(/\s+/).filter((w) => w.length > 0).length;
        if (segDuration <= 1.5 && segWordCount <= 2 && overlapRatioSeg >= 0.25) {
          continue;
        }
      }
    }
    deduped.push(seg);
  }
  return deduped;
}
function preferSeg(seg, last) {
  if (seg.completed && !last.completed) return true;
  if (!seg.completed && last.completed) return false;
  const segDur = parseUTCTimestamp(seg.absolute_end_time).getTime() - parseUTCTimestamp(seg.absolute_start_time).getTime();
  const lastDur = parseUTCTimestamp(last.absolute_end_time).getTime() - parseUTCTimestamp(last.absolute_start_time).getTime();
  return segDur > lastDur;
}
function upsertSegments(existing, incoming) {
  for (const seg of incoming) {
    if (!seg.absolute_start_time || !(seg.text || "").trim()) continue;
    const key = seg.segment_id || seg.absolute_start_time;
    const prev = existing.get(key);
    if (seg.completed && seg.speaker) {
      for (const [k, v] of existing.entries()) {
        if (k === key) continue;
        if (!v.completed && v.speaker === seg.speaker && k.includes(":draft:")) {
          existing.delete(k);
        }
      }
    }
    if (prev) {
      const prevText = (prev.text || "").trim();
      const newText = (seg.text || "").trim();
      const completedChanged = Boolean(prev.completed) !== Boolean(seg.completed);
      if (prevText !== newText || completedChanged) {
        existing.set(key, seg);
        continue;
      }
      if (prev.updated_at && seg.updated_at && prev.updated_at >= seg.updated_at) {
        continue;
      }
    }
    existing.set(key, seg);
  }
  const textIndex = /* @__PURE__ */ new Map();
  for (const [key, seg] of existing.entries()) {
    const textKey = `${seg.speaker || ""}:${(seg.text || "").trim()}`;
    const existingKey = textIndex.get(textKey);
    if (existingKey && existingKey !== key) {
      const prev = existing.get(existingKey);
      if (prev) {
        if (seg.completed && !prev.completed) {
          existing.delete(existingKey);
        } else if (!seg.completed && prev.completed) {
          existing.delete(key);
          continue;
        }
      }
    }
    textIndex.set(textKey, key);
  }
  return existing;
}
function sortSegments(segments) {
  return [...segments].sort(
    (a, b) => a.absolute_start_time.localeCompare(b.absolute_start_time)
  );
}

// src/grouping.ts
var DEFAULT_MAX_CHARS = 512;
function defaultGetGroupKey(segment) {
  return segment.speaker || "Unknown";
}
function groupSegments(segments, options = {}) {
  if (!segments || segments.length === 0) return [];
  const getGroupKey = options.getGroupKey ?? defaultGetGroupKey;
  const maxChars = options.maxCharsPerGroup ?? DEFAULT_MAX_CHARS;
  const sorted = [...segments].sort(
    (a, b) => a.absolute_start_time.localeCompare(b.absolute_start_time)
  );
  const rawGroups = [];
  let current = null;
  for (const seg of sorted) {
    const text = (seg.text || "").trim();
    if (!text) continue;
    const key = getGroupKey(seg);
    if (current && current.key === key) {
      current.segments.push(seg);
    } else {
      if (current) rawGroups.push(current);
      current = { key, segments: [seg] };
    }
  }
  if (current) rawGroups.push(current);
  const groups = [];
  for (const raw of rawGroups) {
    if (raw.segments.length === 0) continue;
    let chunkSegments = [];
    let chunkText = "";
    const flushChunk = () => {
      if (chunkSegments.length === 0) return;
      const first = chunkSegments[0];
      const last = chunkSegments[chunkSegments.length - 1];
      groups.push({
        key: raw.key,
        startTime: first.absolute_start_time,
        endTime: last.absolute_end_time || last.absolute_start_time,
        startTimeSeconds: first.start_time ?? 0,
        endTimeSeconds: last.end_time ?? 0,
        combinedText: chunkText.trim(),
        segments: chunkSegments
      });
      chunkSegments = [];
      chunkText = "";
    };
    for (const seg of raw.segments) {
      const segText = (seg.text || "").trim();
      if (!segText) continue;
      const candidate = chunkText ? `${chunkText} ${segText}` : segText;
      if (chunkSegments.length > 0 && candidate.length > maxChars) {
        flushChunk();
      }
      chunkSegments.push(seg);
      chunkText = chunkText ? `${chunkText} ${segText}` : segText;
    }
    flushChunk();
  }
  return groups;
}
export {
  deduplicateSegments,
  groupSegments,
  parseUTCTimestamp,
  sortSegments,
  upsertSegments
};
