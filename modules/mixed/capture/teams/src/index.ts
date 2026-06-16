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
} from './msteams-speakers';
export type { TeamsSpeakers, TeamsSpeakersOptions, TeamsSpeakerIdentity } from './msteams-speakers';
export { createTeamsChat } from './teams-chat';
export type { TeamsChat, TeamsChatMessage } from './teams-chat';
