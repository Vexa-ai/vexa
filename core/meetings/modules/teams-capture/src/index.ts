/**
 * @vexa/teams-capture — MS Teams' contribution to the mixed lane.
 *
 * Like Zoom, Teams delivers one mixed audio stream (captured by
 * @vexa/mixed-capture-core); this module provides the WHO signal:
 *   - createTeamsSpeakers: watches Teams' voice-level "blue-square" outline to
 *     detect the active speaker → a mixed-capture.v1 `hint` (kind 'dom-outline').
 *   - createTeamsChat: reads the chat panel (content tier).
 */
export {
  createTeamsSpeakers,
  teamsParticipantSelectors,
  teamsNameSelectors,
  teamsParticipantIdSelectors,
  teamsMeetingContainerSelectors,
} from './msteams-speakers.js';
export type {
  TeamsSpeakers,
  TeamsSpeakersOptions,
  TeamsSpeakerIdentity,
  TeamsNameUnresolvedObservation,
} from './msteams-speakers.js';
export {
  TEAMS_PRODUCER_DOM_TRACE_SCHEMA,
  TEAMS_PRODUCER_DOM_TRACE_MAX_RECORDS,
  TEAMS_PRODUCER_DOM_TRACE_MAX_TILES,
  teamsProducerDomTraceNameTokens,
  parseTeamsProducerDomTrace,
  serializeTeamsProducerDomTrace,
  TeamsProducerDomTraceAdmissionError,
} from './producer-dom-trace.js';
export type {
  TeamsProducerDomTrace,
  TeamsProducerDomTraceAdmissionCode,
  TeamsProducerDomTraceHeader,
  TeamsProducerDomTraceTileState,
  TeamsProducerDomTraceNameToken,
} from './producer-dom-trace.js';
export { createTeamsChat } from './teams-chat.js';
export type { TeamsChat, TeamsChatMessage } from './teams-chat.js';
