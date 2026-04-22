/**
 * @file meeting_webinar_interface.h
 * @brief Meeting Service Webinar Interface.
 */

#ifndef _MEETING_WEBINAR_INTERFACE_H_
#define _MEETING_WEBINAR_INTERFACE_H_
#include "zoom_sdk_def.h"
BEGIN_ZOOM_SDK_NAMESPACE

/**
 * @class IMeetingWebinarCtrlEvent
 * @brief Webinar callback event.
 */
class IMeetingWebinarCtrlEvent
{
public:
	virtual ~IMeetingWebinarCtrlEvent() {}
	
	/**
	 * @brief Callback to promote attendees to panelist.
	 * @param result If the promotion is successful, the result is zero(0). Otherwise it is an error code.
	 */
	virtual void onPromptAttendee2PanelistResult(int result) = 0;
	
	/**
	 * @brief Callback to demote attendees to panelist.
	 * @param result If the demotion is successful, the result is zero(0), otherwise an error code.
	 */
	virtual void onDepromptPanelist2AttendeeResult(int result) = 0;
	
	/**
	 * @brief Callback to enable the panelist to start the video.
	 */
	virtual void onAllowPanelistStartVideoNotification() = 0;
	
	/**
	 * @brief Callback to disable the panelist to start the video.
	 */
	virtual void onDisallowPanelistStartVideoNotification() = 0;
	
	/**
	 * @brief Callback event that attendees are required to enable the mic in the view-only mode of webinar.
	 */
	virtual void onSelfAllowTalkNotification() = 0;
	
	/**
	 * @brief Callback event that attendees are required to turn off the mic in the view-only mode of webinar.
	 */
	virtual void onSelfDisallowTalkNotification() = 0;
	
	/**
	 * @brief Callback to enable the attendees to chat. Available only for the host and the co-host.
	 */

	virtual void onAllowAttendeeChatNotification() = 0;
	
	/**
	 * @brief Callback to disable the attendees to chat. Available only for the host and the co-host.
	 */
	virtual void onDisallowAttendeeChatNotification() = 0;
	
	/**
	 * @brief Callback event when emoji reactions status changes.
	 * @param can_reaction true if reactions are allowed, false otherwise.
	 */
	virtual void onAllowWebinarReactionStatusChanged(bool can_reaction) = 0;
	
	/**
	 * @brief Callback event when attendee raise hand status changes.
	 * @param can_raiseHand true if raising hand is allowed, false otherwise.
	 */
	virtual void onAllowAttendeeRaiseHandStatusChanged(bool can_raiseHand) = 0;
	
	/**
	 * @brief Callback event when attendee view participant count status changes.
	 * @param can_viewParticipantCount true if viewing participant count is allowed, false otherwise.
	 */
	virtual void onAllowAttendeeViewTheParticipantCountStatusChanged(bool can_viewParticipantCount) = 0;
	
  	/**
	 * @brief Callback event when attendee's audio status changes. Attendee will receive this callback if their audio status changes.
	 * @param userid The ID of the user whose audio status changes.
	 * @param can_talk true if the user is able to use the audio, false otherwise.  
	 * @param is_muted true if muted, false otherwise. This parameter works only when the value of can_talk is true.
	 */
	virtual void onAttendeeAudioStatusNotification(unsigned int userid, bool can_talk, bool is_muted) = 0;
	
	/**
	 * @brief Callback event when attendee agrees or declines the promote invitation. Host will receive this callback.
	 * @param agree true if the attendee agrees, false otherwise.
	 * @param userid The attendee's user ID.
	 */
	virtual void onAttendeePromoteConfirmResult(bool agree, unsigned int userid) = 0;
};

/** 
 * @brief Webinar Meeting Status.
 * Here are more detailed structural descriptions.
 */
typedef struct tagWebinarMeetingStatus
{
	/** true indicates that the panelist is able to turn on the video. false not. */
	bool allow_panellist_start_video;
	/** true indicates that the attendee is able to chat. false not. */
	bool allow_attendee_chat;
	/** true indicates that the attendee is able to emojireaction. false not. */
	bool allow_emoji_reaction;
	/** true indicates that the attendee is able to raise hand. false not. */
	bool allow_attendee_raise_hand;
	/** true indicates that the attendee is able to view participant count. false not. */
	bool allow_attendee_view_participant_count;
	tagWebinarMeetingStatus()
	{
		Reset();
	}

	void Reset()
	{
		allow_panellist_start_video = false;
		allow_attendee_chat = false;
		allow_emoji_reaction = false;
		allow_attendee_raise_hand = false;
		allow_attendee_view_participant_count = false;
	}
}WebinarMeetingStatus;

/**
 * @brief Enumerations of the panelist chat privilege.
 */
enum SDKPanelistChatPrivilege
{
	/** Allow panelists only to chat with each other. */
	SDKPanelistChatPrivilege_PanelistOnly = 1,	
	/** Allow panelist to chat with everyone. */
	SDKPanelistChatPrivilege_All = 2			
};
#if defined(WIN32)
/**
 * @brief Enumerations of the attendee view display mode.
 */
enum  SDKAttendeeViewMode
{
	/** attendee view display mode is invaild */
	SDKAttendeeViewMode_None,       
	/** follow host */
	SDKAttendeeViewMode_FollowHost, 
	/** always view active speaker */
	SDKAttendeeViewMode_Speaker,   
	/** always view gallery */
	SDKAttendeeViewMode_Gallery,  
	/** attendee can manually switch between gallery and active speaker */
	SDKAttendeeViewMode_Standard,   
	/** attendee view sharing side by side speaker */
	SDKAttendeeViewMode_SidebysideSpeaker, 
	/** attendee view sharing side by side gallery */
	SDKAttendeeViewMode_SidebysideGallery 
};

/**
 * @brief Webinar Legal notices explained.
 * Here are more detailed structural descriptions.
 */
typedef struct tagWebinarLegalNoticesExplainedInfo
{
	const zchar_t* explained_content;
	const zchar_t* url_register_account_owner;
	const zchar_t* url_register_terms;
	const zchar_t* url_register_privacy_policy;
	tagWebinarLegalNoticesExplainedInfo()
	{
		Reset();
	}

	void Reset()
	{
		explained_content = nullptr;
		url_register_account_owner = nullptr;
		url_register_terms = nullptr;
		url_register_privacy_policy = nullptr;
	}
}WebinarLegalNoticesExplainedInfo;
#endif

/**
 * @class IMeetingWebinarController
 * @brief Webinar controller interface
 */
class IMeetingWebinarController
{
public:
	/**
	 * @brief Sets webinar controller callback event handler.
	 * @param pEvent A pointer to the IMeetingWebinarCtrlEvent that receives the webinar callback event. 
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError SetEvent(IMeetingWebinarCtrlEvent* pEvent) = 0;
	
	/**
	 * @brief Promote the attendee to panelist. Available only for the meeting host.
	 * @param userid Specifies the user ID to promote.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 * @note If the function succeeds, the user will receive the IMeetingWebinarCtrlEvent::onPromptAttendee2PanelistResult() callback event.
	 */
	virtual SDKError PromptAttendee2Panelist(unsigned int userid) = 0;
	
	/**
	 * @brief Demote the panelist to attendee. Available only for the host.
	 * @param userid Specifies the user ID to demote.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 * @note If the function succeeds, the user will receive the IMeetingWebinarCtrlEvent::onDepromptPanelist2AttendeeResult() callback event.
	 */
	virtual SDKError DepromptPanelist2Attendee(unsigned int userid) = 0;
	
	/**
	 * @brief Query if the webinar supports the user to use the audio device.
	 * @return If it supports, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 * @deprecated This interface is marked as deprecated.
	 */
	virtual SDKError IsSupportAttendeeTalk() = 0;
	
	/**
	 * @brief The attendee is permitted to use the audio device.
	 * @param userid Specifies the permitted user ID.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 * @note If the function succeeds, the user will receive the IMeetingWebinarCtrlEvent::onAllowAttendeeChatNotification() callback event. Available only for the host.
	 */
	virtual SDKError AllowAttendeeTalk(unsigned int userid) = 0;
	
	/**
	 * @brief Forbid the attendee to use the audio device.
	 * @param userid Specifies the forbidden user ID.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 * @note If the function succeeds, the user will receive the IMeetingWebinarCtrlEvent::onDisallowAttendeeChatNotification() callback event. Available only for the host.
	 */
	virtual SDKError DisallowAttendeeTalk(unsigned int userid) = 0;
	
	/**
	 * @brief The panelist is permitted to start the video.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 * @note If the function succeeds, the user will receive the IMeetingWebinarCtrlEvent::onAllowPanelistStartVideoNotification() callback event. Available only for the host.
	 */
	virtual SDKError AllowPanelistStartVideo() = 0;
	
	/**
	 * @brief Forbid the panelist to start video.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 * @note If the function succeeds, the user will receive the IMeetingWebinarCtrlEvent::onDisallowPanelistStartVideoNotification() callback event. Available only for the host.
	 */
	virtual SDKError DisallowPanelistStartVideo() = 0;
	
	/**
	 * @brief Permitted to use emoji reactions.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 * @note If the function succeeds, the user will receive the IMeetingWebinarCtrlEvent::onAllowWebinarReactionStatusChanged(bool) callback event. Available only for the host.
	 */
	virtual SDKError AllowWebinarEmojiReaction() = 0;
	
	/**
	 * @brief Forbid to use emoji reactions.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 * @note If the function succeeds, the user will receive the IMeetingWebinarCtrlEvent::onAllowWebinarReactionStatusChanged(bool) callback event. Available only for the host.
	 */
	virtual SDKError DisallowWebinarEmojiReaction() = 0;
	
	/**
	 * @brief Determines if current webinar support emoji reaction.
	 * @return true indicates the current webinar supports emoji reactions. Otherwise false.
	 */
	virtual bool IsWebinarEmojiReactionSupported() = 0;
	
	/**
	 * @brief The attendee is permitted to use the raise hand.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 * @note If the function succeeds, the user will receive the IMeetingWebinarCtrlEvent::onAllowAttendeeRaiseHandStatusChanged(bool) callback event. Available only for the host.
	 */
	virtual SDKError AllowAttendeeRaiseHand() = 0;
	
	/**
	 * @brief Forbid the attendee to use the raise hand.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 * @note If the function succeeds, the user will receive the IMeetingWebinarCtrlEvent::onAllowAttendeeRaiseHandStatusChanged(bool) callback event. Available only for the host.
	 */
	virtual SDKError DisallowAttendeeRaiseHand() = 0;
	
	/**
	 * @brief The attendee is permitted to view the participant count.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 * @note If the function succeeds, the user will receive the IMeetingWebinarCtrlEvent::onAllowAttendeeViewTheParticipantCountStatusChanged(bool) callback event. Available only for the host.
	 */
	virtual SDKError AllowAttendeeViewTheParticipantCount() = 0;
	
	/**
	 * @brief Forbid the attendee to view the participant count.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 * @note If the function succeeds, the user will receive the IMeetingWebinarCtrlEvent::onAllowAttendeeViewTheParticipantCountStatusChanged(bool) callback event. Available only for the host.
	 */
	virtual SDKError DisallowAttendeeViewTheParticipantCount() = 0;
	
	/**
	 * @brief Gets the participant count.
     * @return The count of participant.
	 */
	virtual int GetParticipantCount() = 0;
	
	/**
	 * @brief Gets the webinar status.
	 * @return The status of webinar.
	 */
	virtual WebinarMeetingStatus* GetWebinarMeetingStatus() = 0;
	
	/**
	 * @brief Sets the chat privilege of the panelist.
	 * @param privilege The chat priviledge of the panelist.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError SetPanelistChatPrivilege(SDKPanelistChatPrivilege privilege) = 0;
	
	/**
	 * @brief Gets the chat privilege of the panelist.
	 * @param privilege The chat priviledge of the panelist. It validates only when the return value is SDKERR_SUCCESS. 
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError GetPanelistChatPrivilege(SDKPanelistChatPrivilege& privilege) = 0;
	

#if defined(WIN32)
	/**
	 * @brief Sets the view mode of the attendee. Available only for zoom ui.
     * @param mode The view mode of the attendee.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS.. Otherwise, this function returns an error.
	 */
	virtual SDKError SetAttendeeViewMode(SDKAttendeeViewMode mode) = 0;
	
	/**
	 * @brief Gets the view mode of the attendee.Available only for zoom ui.
     * @return If the function succeeds, it will return the attendee's view mode.
	 */
	virtual SDKAttendeeViewMode GetAttendeeViewMode() = 0;
	
	/**
	 * @brief Gets the webinar legal notices prompt.
	 * @return The webinar legal notices prompt.
	 */
	virtual const zchar_t* getWebinarLegalNoticesPrompt() = 0;
	
	/**
	 * @brief Gets the webinar legal notices explained.
	 * @return The webinar legal notices explained.
	 */
	virtual bool getWebinarLegalNoticesExplained(WebinarLegalNoticesExplainedInfo& explained_info) = 0;
#endif
};

END_ZOOM_SDK_NAMESPACE
#endif