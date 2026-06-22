/**
 * @vexa/dash-meetings-list — front door.
 *
 * The ONE entry. Re-exports the presentational meetings-list view + its props type. Consumers import
 * `MeetingsList` and inject `{ meetings, onOpen }`; this brick never fetches, stores, or subscribes.
 */
export { MeetingsList, default } from "./MeetingsList.js";
export type { MeetingsListProps } from "./MeetingsList.js";
