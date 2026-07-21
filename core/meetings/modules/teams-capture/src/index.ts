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
  extractTeamsSpeakerName,
  teamsParticipantSelectors,
  teamsNameSelectors,
  teamsParticipantIdSelectors,
  teamsMeetingContainerSelectors,
} from './msteams-speakers.js';
export type { TeamsSpeakers, TeamsSpeakersOptions, TeamsSpeakerIdentity } from './msteams-speakers.js';
export { createTeamsChat } from './teams-chat.js';
export type { TeamsChat, TeamsChatMessage } from './teams-chat.js';
