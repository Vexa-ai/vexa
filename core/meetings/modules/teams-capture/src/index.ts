/**
 * @vexa/teams-capture — MS Teams' contribution to the mixed lane.
 *
 * Like Zoom, Teams delivers one mixed audio stream (captured by
 * @vexa/mixed-capture-core); this module provides the WHO signal:
 *   - createTeamsSpeakers: watches Teams' voice-level "blue-square" outline to
 *     detect the active speaker → a mixed-capture.v1 `hint` (kind 'dom-outline').
 *   - createTeamsChat: reads the chat panel (content tier).
 *   - createTeamsCaptions: reads Teams' live closed captions when they are ON —
 *     an ALTERNATIVE speaker-attribution SOURCE (Teams attributes each caption to
 *     a named participant itself). Best-effort and DIAGNOSTIC in this iteration:
 *     it feeds neither the transcript nor the mixed-pipeline name binder.
 */
export {
  createTeamsSpeakers,
  extractTeamsSpeakerName,
  isTeamsDisplayNameCandidate,
  isSelfParticipantSurface,
  teamsNameFromStream,
  teamsParticipantSelectors,
  teamsNameSelectors,
  teamsParticipantIdSelectors,
  teamsMeetingContainerSelectors,
  plausibleNameFromLeaves,
} from './msteams-speakers.js';
export type {
  TeamsSpeakers,
  TeamsSpeakersOptions,
  TeamsSpeakerIdentity,
  TeamsNameCandidateContext,
  TeamsSpeakerHealth,
  TeamsSpeakingIndicator,
  TeamsNameUnresolvedObservation,
  TeamsSignalAbsentObservation,
  TeamsIndicatorFiredObservation,
  TeamsIndicatorSilentObservation,
  TeamsProducerObservation,
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
export { createTeamsCaptions, teamsCaptionSelectors } from './msteams-captions.js';
export type {
  TeamsCaptions,
  TeamsCaptionsOptions,
  TeamsCaptionEvent,
  TeamsCaptionsHealth,
  TeamsCaptionsActiveObservation,
  TeamsCaptionsLostObservation,
  TeamsCaptionsAbsentObservation,
  TeamsCaptionSpeakerUnresolvedObservation,
  TeamsCaptionObservation,
} from './msteams-captions.js';
