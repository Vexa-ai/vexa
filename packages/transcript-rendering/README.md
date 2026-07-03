# Transcript Rendering

## Why

Real-time transcript WebSocket streams produce overlapping, out-of-order, duplicate segments. Multiple speakers talk simultaneously, ASR engines emit draft-then-confirmed rewrites, and network jitter delivers segments out of order. Without a processing pipeline, rendering this raw data produces garbled, duplicated text.

## What

This library transforms raw `TranscriptSegment[]` streams into clean, speaker-grouped `SegmentGroup[]` output ready for rendering.

### Data Flow

```
WebSocket / REST segments
        │
        ▼
  upsertSegments()        merge into Map, handle draft→confirmed
        │
        ▼
  sortSegments()          order by absolute_start_time
        │
        ▼
  deduplicateSegments()   remove overlaps, expansions, tail-repeats (per-speaker)
        │
        ▼
  groupSegments()         consecutive same-speaker segments → SegmentGroup[]
        │
        ▼
  SegmentGroup[]          ready to render
```

### Exports

| Export | Signature | Description |
|--------|-----------|-------------|
| `upsertSegments` | `(existing: Map<string, T>, incoming: T[]) => Map<string, T>` | Merge incoming segments into a map; handles draft→confirmed transitions |
| `sortSegments` | `(segments: T[]) => T[]` | Sort segments by `absolute_start_time` (ISO string comparison) |
| `deduplicateSegments` | `(segments: T[]) => T[]` | Speaker-aware dedup: adjacent duplicates, containment, expansion, tail-repeats |
| `groupSegments` | `(segments: T[], options?: GroupingOptions) => SegmentGroup<T>[]` | Group consecutive same-key segments; splits at `maxCharsPerGroup` boundaries |
| `parseUTCTimestamp` | `(timestamp: string) => Date` | Parse ISO timestamps as UTC (appends `Z` when no timezone suffix) |
| `TranscriptSegment` | type | Input segment interface |
| `SegmentGroup` | type | Output grouped segments |
| `GroupingOptions` | type | Grouping configuration |

### TranscriptSegment Fields

| Field | Type | Description |
|-------|------|-------------|
| `text` | `string` | Segment text content |
| `speaker` | `string?` | Speaker name or identifier |
| `absolute_start_time` | `string` | ISO timestamp of segment start |
| `absolute_end_time` | `string` | ISO timestamp of segment end |
| `completed` | `boolean?` | Whether the segment is finalized (vs. draft) |
| `segment_id` | `string?` | Stable identity (e.g., `speakerA:3`) |
| `start_time` | `number?` | Relative start time in seconds |
| `end_time` | `number?` | Relative end time in seconds |
| `updated_at` | `string?` | ISO timestamp of last update |

### GroupingOptions

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `getGroupKey` | `(segment: TranscriptSegment) => string` | Groups by `speaker` | Returns the grouping key for a segment |
| `maxCharsPerGroup` | `number` | `512` | Maximum characters per group before splitting at segment boundaries |

## How

### Install & Build

```bash
cd packages/transcript-rendering
npm install
npm run build      # Build with tsup (ESM + CJS)
npm test           # Run tests with vitest
npm run typecheck  # Type-check without emitting
```

### Usage

```typescript
import {
  upsertSegments,
  sortSegments,
  deduplicateSegments,
  groupSegments,
  type TranscriptSegment,
} from '@vexaai/transcript-rendering';

// Maintain a segment map across WebSocket messages
const segments = new Map<string, TranscriptSegment>();

ws.on('message', (data) => {
  const incoming: TranscriptSegment[] = JSON.parse(data);

  // Full pipeline: upsert → sort → dedup → group
  upsertSegments(segments, incoming);
  const sorted = sortSegments([...segments.values()]);
  const deduped = deduplicateSegments(sorted);
  const groups = groupSegments(deduped);

  // Each group has: key (speaker), combinedText, startTime, endTime, segments[]
  render(groups);
});
```

### Package

Published as `@vexaai/transcript-rendering`. Dual ESM/CJS output via tsup. Apache-2.0 license.
