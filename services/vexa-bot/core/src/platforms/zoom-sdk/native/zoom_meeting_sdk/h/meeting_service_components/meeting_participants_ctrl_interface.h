/**
 * @file meeting_participants_ctrl_interface.h
 * @brief Meeting Participants Controller Interface. 
 */
#ifndef _MEETING_ParticipantsCtrl_INTERFACE_H_
#define _MEETING_ParticipantsCtrl_INTERFACE_H_
#include "zoom_sdk_def.h"
#include "meeting_service_components/meeting_recording_interface.h"
#if defined(WIN32)
#include "meeting_service_components/meeting_emoji_reaction_interface.h"
#endif
BEGIN_ZOOM_SDK_NAMESPACE
/** 
 * @brief Enumeration of user role.
 * Here are more detailed structural descriptions.
 */
enum UserRole
{
	/** For initialization. */
	USERROLE_NONE,
	/** Role of the host. */
	USERROLE_HOST,
	/** Role of co-host. */
	USERROLE_COHOST,
	/** Role of the panelist, valid only in webinar. */
	USERROLE_PANELIST,
	/** Host role in breakout room. */
	USERROLE_BREAKOUTROOM_MODERATOR,
	/** Role of attendee. */
	USERROLE_ATTENDEE,
};

/**
 * @brief Status of webinar attendee.
 * Here are more detailed structural descriptions.
 */
typedef struct tagWebinarAttendeeStatus
{
	/** true indicates that it is able to talk. */
	bool allow_talk;
	tagWebinarAttendeeStatus()
	{
		allow_talk = false;
	}
}WebinarAttendeeStatus;

/**
 * @brief Enumeration of focus mode type.
 * Here are more detailed structural descriptions.
 */
enum FocusModeShareType
{
	FocusModeShareType_None,
	FocusModeShareType_HostOnly,
	FocusModeShareType_AllParticipants,
};

/**
 * @brief Info of virtual name tag.
 * Here are more detailed structural descriptions.
 */
typedef struct tagZoomSDKVirtualNameTag
{
	/** Tag ID.tagID is the unique identifier.The range of tagID is 0 - 1024. */
	int tagID; 
	/** Tag name. */
	const zchar_t* tagName;   

	tagZoomSDKVirtualNameTag()
	{
		tagID = 0;
		tagName = nullptr;
	}
}ZoomSDKVirtualNameTag;

/**
 * @class IUserInfo
 * @brief User information interface.
 */
class IUserInfo
{
public:
	/**
	 * @brief Gets the username matched with the current user information.
	 * @return If the function succeeds, it returns the username. Otherwise, this function fails and returns nullptr.
	 * @note Valid for both normal user and webinar attendee.
	 */
	virtual const zchar_t* GetUserName() = 0;
	
	/**
	 * @brief Determines whether the member corresponding with the current information is the host or not.
	 * @return true if the user is the host. Otherwise, false.
	 */
	virtual bool IsHost() = 0;
	
	/**
	 * @brief Gets the user ID matched with the current user information.
	 * @return If the function succeeds, it returns the user ID. Otherwise, this function returns ZERO(0).
	 * @note Valid for both normal user and webinar attendee.
	 */
	virtual unsigned int GetUserID() = 0;
	
	/**
	 * @brief Gets the avatar file path matched with the current user information.
	 * @return If the function succeeds, it returns the avatar file path. Otherwise, this function fails and returns nullptr.
	 */
	virtual const zchar_t* GetAvatarPath() = 0;
	
	/**
	 * @brief Gets the user persistent ID matched with the current user information.
	 * @return If the function succeeds, it returns the user persistent ID. Otherwise, this function fails and returns nullptr.
	 */
	virtual const zchar_t* GetPersistentId() = 0;
	
	/**
	 * @brief Gets the customer_key matched with the current user information.
	 * @return If the function succeeds, it returns the customer_key. Otherwise, this function fails and returns nullptr.
	 * @note If you assign a customer_key for a user in the start or join meeting parameter, the value you assigned will be returned. Otherwise, an empty string will be returned.
	 */
	virtual const zchar_t* GetCustomerKey() = 0;
	
	/**
	 * @brief Determines the video status of the user specified by the current information.
	 * @return true if the video is turned on. Otherwise, false.
	 * @note Valid for both normal user and webinar attendee.
	 */
	virtual bool IsVideoOn() = 0;
	
	/**
	 * @brief Determines the audio status of the user specified by the current information.
	 * @return true if the audio status is muted. Otherwise, false.
	 */
	virtual bool IsAudioMuted() = 0;
	
	/**
	 * @brief Gets the audio type of the user specified by the current information when joining the meeting.
	 * @return If the function succeeds, it returns the type of audio when the user joins the meeting.
	 */
	virtual AudioType GetAudioJoinType() = 0;
	
	/**
	 * @brief Determines whether the current information corresponds to the user himself or not.
	 * @return true if the current information corresponds to the user himself. Otherwise, false.
	 */
	virtual bool IsMySelf() = 0;
	
	/**
	 * @brief Determines whether the user specified by the current information is in the waiting room or not.
	 * @return true if the specified user is in the waiting room. Otherwise, false.
	 */
	virtual bool IsInWaitingRoom() = 0;
	
	/**
	 * @brief Determines whether the user specified by the current information raises hand or not.
	 * @return true if the user raises hand. Otherwise, false.
	 */
	virtual bool IsRaiseHand() = 0;
	
	/**
	 * @brief Gets the type of role of the user specified by the current information.
	 * @return If the function succeeds, it returns the role of the user.
	 */
	virtual UserRole GetUserRole() = 0;
	
	/**
	 * @brief Determines whether the user corresponding to the current information joins the meeting by telephone or not.
	 * @return true if the user joins the meeting by telephone. Otherwise, false.
	 */
	virtual bool IsPurePhoneUser() = 0;
	
	/**
	 * @brief Gets the mic level of the user corresponding to the current information.
	 * @return If the function succeeds, it returns the mic level of the user.
	 */
	virtual int GetAudioVoiceLevel() = 0;
	
	/**
	 * @brief Determines whether the user corresponding to the current information is the sender of Closed Caption or not.
	 * @return true if the user is the sender of Closed Caption. Otherwise, false.
	 */
	virtual bool IsClosedCaptionSender() = 0;
	
	/**
	 * @brief Determines whether the user specified by the current information is talking or not.
	 * @return true if the specified user is talking. Otherwise, false.
	 */
	virtual bool IsTalking() = 0;
	
	/**
	 * @brief Determines whether the user specified by the current information is H323 user or not.
	 * @return true if the specified user is H323 user. Otherwise, false.
	 */
	virtual bool IsH323User() = 0;
	
	/**
	 * @brief Gets the webinar status of the user specified by the current information.
	 * @return If the function succeeds, it returns the status of the specified user. Otherwise, this function fails and returns nullptr.
	 */
	virtual WebinarAttendeeStatus* GetWebinarAttendeeStatus() = 0;
	
#if defined(WIN32)
	/**
	 * @brief Determines whether the user specified by the current information is a interpreter or not.
	 * @return true if the specified user is an interpreter. Otherwise, false.
	 */
	virtual bool IsInterpreter() = 0;
	
	/**
	 * @brief Determines whether the user specified by the current information is a sign language interpreter or not.
	 * @return true if the specified user is a sign language interpreter. Otherwise, false.
	 */
	virtual bool IsSignLanguageInterpreter() = 0;
	
	/**
	 * @brief Gets the active language, if the user is a interpreter.
	 * @return If success, the return value is the active language abbreviation, Otherwise the return value is ZERO(0).
	 */
	virtual const zchar_t* GetInterpreterActiveLanguage() = 0;
	
	/**
	 * @brief Gets the emoji feedback type of the user.
	 * @return The emoji feedback type of the user.
	 */
	virtual SDKEmojiFeedbackType GetEmojiFeedbackType() = 0;
	
	/**
	 * @brief Determines whether the user specified by the current information in companion mode or not.
	 * @return true if the specified user is in companion mode. Otherwise, false.
	 */
	virtual bool IsCompanionModeUser() = 0;
#endif

	/**
	 * @brief Gets the local recording status.
	 * @return The status of the local recording status.
	 */
	virtual RecordingStatus GetLocalRecordingStatus() = 0;
	
	/**
	 * @brief Determines whether the user has started a raw live stream.
	 * @return true if the specified user has started a raw live stream. Otherwise, false.
	 */
	virtual bool IsRawLiveStreaming() = 0;
	
	/**
	 * @brief Determines whether the user has raw live stream privilege.
	 * @return true if the specified user has raw live stream privilege. Otherwise, false.
	 */
	virtual bool HasRawLiveStreamPrivilege() = 0;
	
	/**
	 * @brief Query if the participant has a camera.
	 * @return true if the user has a camera. Otherwise, false.
	 */
	virtual bool HasCamera() = 0;
	
	/**
	 * @brief Determines whether the user is production studio user.
	 * @return true if the specified user is production studio user. Otherwise, false.
	 */
	virtual bool IsProductionStudioUser() = 0;
	
	/**
	 * @brief Determines whether the user specified by the current information is in the webinar backstage or not.
	 * @return true if the specified user is in the webinar backstage. Otherwise, false.
	 */
	virtual bool IsInWebinarBackstage() = 0;
	
	/**
	 * @brief Gets the parent user ID of the production studio user.
	 * @note Just production studio user has parent. 
	 */
	virtual unsigned int GetProductionStudioParent() = 0;
	
	/**
	 * @brief Determines whether the user specified by the current information is bot user or not.
	 * @note This function return true only when using Meeting SDK with an On-Behalf token; Otherwise it will always return false.
	 * @return true if the specified user is a bot user. Otherwise, false.
	 */
	virtual bool IsBotUser() = 0;
	
	/**
	 * @brief Gets the bot app name.
	 * @return If the function succeeds, it returns the bot app name. Otherwise, this function fails and returns nullptr.
	 */
	virtual const zchar_t* GetBotAppName() = 0;
	
	/**
	 * @brief Query if the participant enabled virtual name tag.
	 * @return true if enabled. Otherwise, false.
	 */
	virtual bool IsVirtualNameTagEnabled() = 0;
	
	/**
	 * @brief Query the virtual name tag roster infomation.
	 * @return If the function succeeds, it return the list of user's virtual name tag roster info.
	 */
	virtual IList<ZoomSDKVirtualNameTag>* GetVirtualNameTagList() = 0;

	/**
	 * @brief Query the granted assets info when assign a co-owner.
	 * @return If the function succeeds, it returns the list of user's grant assets info.
	 * @note If not granted any assets privilege, the default configuration of the web will be queried. If has granted assets privilege, the result after granting will be queried.
	 */
	 virtual IList<GrantCoOwnerAssetsInfo>* GetGrantCoOwnerAssetsInfo() = 0;

	/**
	 * @brief Determines whether the specified user is an audio only user.
	 * @return true if the specified user is an audio only user. Otherwise, false.
	 */
	virtual bool IsAudioOnlyUser() = 0;

	virtual ~IUserInfo(){};
};

/**
 * @class IMeetingParticipantsCtrlEvent
 * @brief Meeting Participants Controller Callback Event.
 */
class IMeetingParticipantsCtrlEvent
{
public:
	virtual ~IMeetingParticipantsCtrlEvent() {}
	/**
	 * @brief Callback event of notification of users who are in the meeting.
	 * @param lstUserID List of user IDs. 
	 * @param strUserList List of users in JSON format. This function is currently invalid, hereby only for reservations.
	 * @note Valid for both normal user and webinar attendee.
	 */
	virtual void onUserJoin(IList<unsigned int >* lstUserID, const zchar_t* strUserList = nullptr) = 0;
	
	/**
	 * @brief Callback event of notification of user who leaves the meeting.
	 * @param lstUserID List of the user ID who leaves the meeting.
	 * @param strUserList List of the users in JSON format. This function is currently invalid, hereby only for reservations.
	 * @note Valid for both normal user and webinar attendee.
	 */
	virtual void onUserLeft(IList<unsigned int >* lstUserID, const zchar_t* strUserList = nullptr) = 0;
	
	/**
	 * @brief Callback event of notification of the new host. 
	 * @param userId Specify the ID of the new host. 
	 */
	virtual void onHostChangeNotification(unsigned int userId) = 0;
	
	/**
	 * @brief Callback event of changing the state of the hand.
	 * @param bLow true indicates to put down the hand, false indicates to raise the hand. 
	 * @param userid Specify the user ID whose status changes.
	 */
	virtual void onLowOrRaiseHandStatusChanged(bool bLow, unsigned int userid) = 0;
	
	/**
	 * @brief Callback event of changing the screen name. 
	 * @param userId list Specify the users ID whose status changes.
	 * @note Valid for both normal user and webinar attendee.
	 */
	virtual void onUserNamesChanged(IList<unsigned int>* lstUserID) = 0;
	
	/**
	 * @brief Callback event of changing the co-host.
	 * @param userId Specify the user ID whose status changes. 
	 * @param isCoHost true indicates that the specified user is co-host.
	 */
	virtual void onCoHostChangeNotification(unsigned int userId, bool isCoHost) = 0;
	
	/**
	 * @brief Callback event of invalid host key.
	 */
	virtual void onInvalidReclaimHostkey() = 0;
	
	/**
	 * @brief Callback event of the host calls the lower all hands interface, the host/cohost/panelist will receive this callback.
	 */
	virtual void onAllHandsLowered() = 0;
	
	/**
	 * @brief Callback event that the status of local recording changes.
	 * @param userId Specify the user ID whose status changes. 
	 * @param status Value of recording status.
	 */
	virtual void onLocalRecordingStatusChanged(unsigned int user_id, RecordingStatus status) = 0;
	
	/**
	 * @brief Callback event that lets participants rename themself.
	 * @param bAllow true allow. If false, participants may not rename themselves.
	 */
	virtual void onAllowParticipantsRenameNotification(bool bAllow) = 0;
	
	/**
	 * @brief Callback event that lets participants unmute themself.
	 * @param bAllow true allow. If false, participants may not rename themselves.
	 */
	virtual void onAllowParticipantsUnmuteSelfNotification(bool bAllow) = 0;
	
	/**
	 * @brief Callback event that lets participants start a video.
	 * @param bAllow true allow. If false, disallow.
	 */
	virtual void onAllowParticipantsStartVideoNotification(bool bAllow) = 0;
	
	/**
	 * @brief Callback event that lets participants share a new whiteboard.
	 * @param bAllow true allow. If false, participants may not share new whiteboard.
	 */
	virtual void onAllowParticipantsShareWhiteBoardNotification(bool bAllow) = 0;
	
	/**
	 * @brief Callback event that the request local recording privilege changes.
	 * @param status Value of request local recording privilege status.
	 */
	virtual void onRequestLocalRecordingPrivilegeChanged(LocalRecordingRequestPrivilegeStatus status) = 0;
	
	/**
	 * @brief Callback event that lets participants request that the host starts cloud recording.
	 * @param bAllow true allow. If false, disallow.
	 */
	virtual void onAllowParticipantsRequestCloudRecording(bool bAllow) = 0;
	
	/**
	 * @brief Callback event that the user avatar path is updated in the meeting.
	 * @param userID Specify the user ID whose avatar updated. 
	 */
	virtual void onInMeetingUserAvatarPathUpdated(unsigned int userID) = 0;
	
	/**
	 * @brief Callback event that participant profile status change.
	 * @param bHide true indicates hide participant profile picture, false means show participant profile picture. 
	 */
	virtual void onParticipantProfilePictureStatusChange(bool bHidden) = 0;
	
	/**
	 * @brief Callback event that focus mode changed by host or co-host.
	 * @param bEnabled true indicates the focus mode change to on. Otherwise off.
	 */
	virtual void onFocusModeStateChanged(bool bEnabled) = 0;
	
	/**
	 * @brief Callback event that that focus mode share type changed by host or co-host.
	 * @param type Share type change.
	 */
	virtual void onFocusModeShareTypeChanged(FocusModeShareType type) = 0;
	
	/**
	 * @brief Callback event that the bot relationship changed in the meeting.
	 * @param authorizeUserID Specify the authorizer user ID.
	 */
	virtual void onBotAuthorizerRelationChanged(unsigned int authorizeUserID) = 0;
	
	/**
	 * @brief Notification of virtual name tag status change.
	 * @param bOn true indicates virtual name tag is turn on, Otherwise not.
	 * @param userID The ID of user who virtual name tag status changed.
	 */
	virtual void onVirtualNameTagStatusChanged(bool bOn, unsigned int userID) = 0;
	
	/**
	 * @brief Notification of virtual name tag roster info updated.
	 * @param userID The ID of user who virtual name tag status changed.
	 */
	virtual void onVirtualNameTagRosterInfoUpdated(unsigned int userID) = 0;

#if defined(WIN32)
	/**
	 * @brief Callback event that the companion relationship created in the meeting.
	 * @param parentUserID Specify the parent user ID.
	 * @param childUserID Specify the child user ID.
	 */
	virtual void onCreateCompanionRelation(unsigned int parentUserID, unsigned int childUserID) = 0;
	
	/**
	 * @brief Callback event that the companion relationship removed in the meeting.
	 * @param childUserID Specify the child user ID.
	 */
	virtual void onRemoveCompanionRelation(unsigned int childUserID) = 0;
#endif

	/**
	 * @brief Callback event when the user's grant co-owner permission changed.
	 * @param canGrantOther true indicates can grant others,otherwise not.
	 */
	 virtual void onGrantCoOwnerPrivilegeChanged(bool canGrantOther) = 0;
};

/**
 * @class IMeetingParticipantsController
 * @brief Meeting waiting room controller interface
 */
class IMeetingParticipantsController
{
public:
	/**
	 * @brief Sets the participants controller callback event handler.
	 * @param pEvent A pointer to the IParticipantsControllerEvent that receives the participants event. 
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError SetEvent(IMeetingParticipantsCtrlEvent* pEvent) = 0;
	
	/**
	 * @brief Gets the list of all the panelists in the meeting.
	 * @return If the function succeeds, the return value is the list of the panelists in the meeting. Otherwise, the return value is nullptr.
	 * @note Valid for both ZOOM style and user custom interface mode. Valid for both normal user and webinar attendee.
	 */
	virtual IList<unsigned int >* GetParticipantsList() = 0;
	
	/**
	 * @brief Gets the information of specified user.
	 * @param userid Specify the user ID for which you want to get the information. 
	 * @return If the function succeeds, the return value is a pointer to the IUserInfo. Otherwise, the return value is nullptr.
	 * @note Valid for both ZOOM style and user custom interface mode. Valid for both normal user and webinar attendee.
	 */
	virtual IUserInfo* GetUserByUserID(unsigned int userid) = 0;
	
	/**
	 * @brief Gets the information of current user.
	 * @return If the function succeeds, the return value is a pointer to the IUserInfo. Otherwise, the return value is nullptr.
	 * @note Valid for both ZOOM style and user custom interface mode..
	 */
	virtual IUserInfo* GetMySelfUser() = 0;
	
	/**
	 * @brief Gets the information about the bot's authorized user.
	 * @param userid Specify the user ID for which to get the information. 
	 * @return If the function succeeds, the return value is a pointer to the IUserInfo. Otherwise, the return value is nullptr.
	 * @note Valid for both ZOOM style and user custom interface mode.
	 */
	virtual IUserInfo* GetBotAuthorizedUserInfoByUserID(unsigned int userid) = 0;
	
	/**
	 * @brief Gets the authorizer's bot list.
	 * @param userid Specify the user ID for which to get the information. 
	 * @return If the function succeeds, the return value is the authorizer's bot list in the meeting. Otherwise, the return value is nullptr.
	 * @note Valid for both ZOOM style and user custom interface mode.
	 */
	virtual IList<unsigned int >* GetAuthorizedBotListByUserID(unsigned int userid) = 0;
	

#if defined(WIN32)
	/**
	 * @brief Gets the information about the user's parent user.
	 * @param userid Specify the user ID for which to get the information. 
	 * @return If the function succeeds, the return value is a pointer to the IUserInfo. Otherwise, the return value is nullptr.
	 * @note Valid for both ZOOM style and user custom interface mode.
	 */
	virtual IUserInfo* GetCompanionParentUser(unsigned int userid) = 0;
	
	/**
	 * @brief Gets the user's child list.
	 * @param userid Specify the user ID for which to get the information. 
	 * @return If the function succeeds, the return value is the sub-user list of user companion mode. Otherwise, the return value is nullptr.
	 * @note Valid for both ZOOM style and user custom interface mode.
	 */
	virtual IList<unsigned int >* GetCompanionChildList(unsigned int userid) = 0;
#endif

	/**
	 * @brief Cancel all hands raised.
	 * @param forWebinarAttendees is true, the SDK sends the lower all hands command only to webinar attendees.
	 * forWebinarAttendees is false, the SDK sends the lower all hands command to anyone who is not a webinar attendee, such as the webinar host/cohost/panelist or everyone in a regular meeting.. 
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 * @note Valid for both ZOOM style and user custom interface mode..
	 */
	virtual SDKError LowerAllHands(bool forWebinarAttendees) = 0;
	
	/**
	 * @brief Change the screen name of specified user. Only the host or co-host can change the others' name.
	 * @param userid Specify the user ID whose name needed to be changed. 
	 * @param userName Specify a new screen name for the user.
	 * @param bSaveUserName Save the screen name to join the meeting next time.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 * @note Valid for both ZOOM style and user custom interface mode..
	 */
	virtual SDKError ChangeUserName(const unsigned int userid, const zchar_t* userName, bool bSaveUserName) = 0;
	
	/**
	 * @brief Cancel the hands raised of specified user.
	 * @param userid Specify the user ID to put down the hands.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 * @note Valid for both ZOOM style and user custom interface mode..
	 */
	virtual SDKError LowerHand(unsigned int userid) = 0;
	
	/**
	 * @brief Raise hands in the meeting.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 * @note Valid for both ZOOM style and user custom interface mode..
	 */
	virtual SDKError RaiseHand() = 0;
	
	/**
	 * @brief Sets the specified user as the host.
	 * @param userid Specify the user ID to be the host.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 * @note Valid for both ZOOM style and user custom interface mode..
	 */
	virtual SDKError MakeHost(unsigned int userid) = 0;
	
	/**
	 * @brief Determines if it is able to change the specified user role as the co-host.
	 * @param userid Specify the user ID.
	 * @return If the specified user can be the co-host, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 * @note Valid for both ZOOM style and user custom interface mode..
	 */
	virtual SDKError CanbeCohost(unsigned int userid) = 0;
	
	/**
	 * @brief Sets the specified user as the co-host.
	 * @param userid Specify the user ID who is to be the co-host.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 * @note Valid for both ZOOM style and user custom interface mode..
	 */
	virtual SDKError AssignCoHost(unsigned int userid) = 0;
	
	/**
	 * @brief Gets back the co-host role from the specified user.
	 * @param userid Specify the user ID to get back the co-host.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 * @note Valid for both ZOOM style and user custom interface mode..
	 */
	virtual SDKError RevokeCoHost(unsigned int userid) = 0;
	
	/**
	 * @brief Expel the specified user.
	 * @param userid Specify the ID of user to be expelled.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 * @note Valid for both ZOOM style and user custom interface mode..
	 */
	virtual SDKError ExpelUser(unsigned int userid) = 0;
	
	/**
	 * @brief Checks whether myself is original host.
	 * @return true if the current user is the original host. Otherwise, false.
	 */
	virtual bool IsSelfOriginalHost() = 0;
	
	/**
	 * @brief Reclaim the role of the host.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 * @note Valid only for Zoom style user interface mode.
	 */
	virtual SDKError ReclaimHost() = 0;
	
	/**
	 * @brief Determines if the user has the right to reclaim the host role.
	 * @param [out] bCanReclaimHost true indicates to have the right to reclaim the host role.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 * @note Valid for both ZOOM style and user custom interface mode..
	 */
	virtual SDKError CanReclaimHost(bool& bCanReclaimHost) = 0;
	
	/**
	 * @brief Reclaim role of host via host_key.
	 * @param host_key The key to get the role of host.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 * @note Valid for both ZOOM style and user custom interface mode..
	 */
	virtual SDKError ReclaimHostByHostKey(const zchar_t* host_key) = 0;

	virtual SDKError AllowParticipantsToRename(bool bAllow) = 0;

	virtual bool IsParticipantsRenameAllowed() = 0;

	virtual SDKError AllowParticipantsToUnmuteSelf(bool bAllow) = 0;

	virtual bool IsParticipantsUnmuteSelfAllowed() = 0;

	virtual SDKError AskAllToUnmute() = 0;
	
	/**
	 * @brief Allowing the regular attendees to start video, it can only be used in regular meeetings(no bo).
	 * @param bAllow true indicates Allowing the regular attendees to start video. 
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError AllowParticipantsToStartVideo(bool bAllow) = 0;
	
	/**
	 * @brief Checks whether the current meeting allows participants to start video, it can only be used in regular meeetings(no bo).
	 * @return If allows participants to start video, the return value is true.
	 */
	virtual bool IsParticipantsStartVideoAllowed() = 0;
	
	/**
	 * @brief Allowing the regular attendees to share whiteboard, it can only be used in regular meeetings(no bo).
	 * @param bAllow true indicates Allowing the regular attendees to share whiteboard. 
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError AllowParticipantsToShareWhiteBoard(bool bAllow) = 0;
	
	/**
	 * @brief Checks whether the current meeting allows participants to share whiteboard, it can only be used in regular meeetings(no bo).
	 * @return If allows participants to share whiteboard, the return value is true.
	 */
	virtual bool IsParticipantsShareWhiteBoardAllowed() = 0;
	
	/**
	 * @brief Allowing the regular attendees to use chat, it can only be used in regular meeetings(no webinar or bo).
	 * @param bAllow true indicates Allowing the regular attendees to use chat. 
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 * @note Valid for both ZOOM style and user custom interface mode..
	 */
	virtual SDKError AllowParticipantsToChat(bool bAllow) = 0;
	
	/**
	 * @brief  Check whether the current meeting allows participants to chat, it can only be used in regular meeetings(no webinar or bo)..
	 * @return If allows participants to chat, the return value is true.
	 * @note Valid for both ZOOM style and user custom interface mode..
	 */
	virtual bool IsParticipantAllowedToChat() = 0;
	
	/**
	 * @brief Checks whether the current meeting allows participants to send local recording privilege request, it can only be used in regular meeetings(no webinar or bo).
	 * @return If allows participants to send request, the return value is true.
	 */
	virtual bool IsParticipantRequestLocalRecordingAllowed() = 0;
	
	/**
	 * @brief Allowing the regular attendees to send local recording privilege request, it can only be used in regular meeetings(no bo).
	 * @param bAllow true indicates Allowing the regular attendees to send local recording privilege request. 
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError AllowParticipantsToRequestLocalRecording(bool bAllow) = 0;
	
	/**
	 * @brief Checks whether the current meeting auto grant participants local recording privilege request, it can only be used in regular meeetings(no webinar or bo).
	 * @return If auto grant participants local recording privilege request, the return value is true.
	 */
	virtual bool IsAutoAllowLocalRecordingRequest() = 0;
	
	/**
	 * @brief Auto grant or deny the regular attendee's local recording privilege request, it can only be used in regular meeetings(no bo).
	 * @param bAllow true indicates Auto grant or deny the regular attendee's local recording privilege request. 
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError AutoAllowLocalRecordingRequest(bool bAllow) = 0;
	
	/**
	 * @brief Determines if the current user can hide participant profile pictures.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError CanHideParticipantProfilePictures() = 0;
	
	/**
	 * @brief Checks whether the current meeting hides participant pictures.
	 * @return If participants profile pictures be hidden, the return value is true.
	 */
	virtual bool IsParticipantProfilePicturesHidden() = 0;
	
	/**
	 * @brief Hide/Show participant profile pictures.
	 * @param bHide true indicates Hide participant profile pictures. 
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError HideParticipantProfilePictures(bool bHide) = 0;
	
	/**
	 * @brief Determines if the focus mode enabled or not by web portal.
	 * @return true indicates focus mode enabled. Otherwise not.
	 */
	virtual bool IsFocusModeEnabled() = 0;
	
	/**
	 * @brief Determines if the focus mode on or off.
	 * @return true indicates focus mode on. Otherwise off.
	 */
	virtual bool IsFocusModeOn() = 0;
	
	/**
	 * @brief Turn focus mode on or off. Focus mode on means Participants will only be able to see hosts' videos and shared content, and videos of spotlighted participants.
	 * @param turnOn true indicates to turen on, false means to turn off.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError TurnFocusModeOn(bool turnOn) = 0;
	
	/**
	 * @brief Gets focus mode share type indicating who can see the shared content which is controlled by host or co-host.
	 * @return The current focus mode share type.
	 */
	virtual FocusModeShareType GetFocusModeShareType() = 0;
	
	/**
	 * @brief Sets the focus mode type indicating who can see the shared content which is controlled by host or co-host.
	 * @param shareType The type of focus mode share type.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError SetFocusModeShareType(FocusModeShareType shareType) = 0;
	
	/**
	 * @brief Determines if the current user can enable participant request clould recording.
	 * @return true if the current user can enable participant request cloud recording. Otherwise, false.
	 */
	virtual bool CanEnableParticipantRequestCloudRecording() = 0;
	
	/**
	 * @brief Checks whether the current meeting allows participants to send cloud recording privilege request, This can only be used in regular meeetings and webinar(no breakout rooms).
	 * @return If allows participants to send request, the return value is true.
	 */
	virtual bool IsParticipantRequestCloudRecordingAllowed() = 0;
	
	/**
	 * @brief Toggle whether attendees can requests for the host to start a cloud recording. This can only be used in regular meeetings and webinar(no breakout rooms).
	 * @param bAllow true indicates that participants are allowed to send cloud recording privilege requests.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError AllowParticipantsToRequestCloudRecording(bool bAllow) = 0;
	
	/**
	 * @brief Determines if support virtual name tag feature.
	 * @return true if supports the virtual name tag feature. Otherwise, false.
	 */
	virtual bool IsSupportVirtualNameTag() = 0;
	
	/**
	 * @brief Enables the virtual name tag feature for the account.
	 * @param bEnabled true indicates enabled, Otherwise not.
	 * @return If the function succeeds, it return SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError EnableVirtualNameTag(bool bEnabled) = 0;
	
	/**
	 * @brief Prepare to Update virtual name tag roster infomation.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 * @note If the function succeeds, all the created virtual name tag roster will be removed.
	 */
	virtual SDKError CreateVirtualNameTagRosterInfoBegin() = 0;
	
	/**
	 * @brief Add the userRoster to a prepared list.
	 * @param userRoster, The virtual name tag roster info list for specify user.
	 * @return true if the userRoster is added to the prepared list successfully.
	 * @note The maximum size of userRoster should less 20. User should sepcify the tagName and tagID of echo ZoomSDKVirtualNameTag object. The range of tagID is 0-1024.
	 */
	virtual bool AddVirtualNameTagRosterInfoToList(ZoomSDKVirtualNameTag userRoster) = 0;
	
	/**
	 * @brief Batch create virtual name tag roster infoTo according to the prepare list.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 * @note CreateVirtualNameTagRosterInfoBegin() must be called before this function is called. Otherwise SDKErr_WRONG_USAGE will be returned.
	 */
	virtual SDKError CreateVirtualNameTagRosterInfoCommit() = 0;

	/**
	 * @brief Query if the user can be assigned as co-owner in meeting. Co-owner can be grant with privilege to manage some assets after the meeting.
	 * @param userid The ID of user who will be assigned as co-owner in meeting.
	 * @return true if the user can be assigned as co-owner. Otherwise, false.
	 */
	 virtual bool CanBeCoOwner(unsigned int userid) = 0;

	 /**
	  * @brief Assigns a user as co-host and grants privileges to manage assets after the meeting.
	  * @param userid The ID of user to be assigned as co-host.
	  * @param list A List of \link GrantCoOwnerAssetsInfo \endlink struct representing the assets and privileges to grant.
	  * @return If the function succeeds, it will return SDKERR_SUCCESS. Otherwise, this function returns an error.
	  * @note The co-host cannot be assigned as co-host by himself. And the user should have the power to assign the role.
	  */
	 virtual SDKError AssignCoHostWithAssetsPrivilege(unsigned int userid, IList<GrantCoOwnerAssetsInfo>* list) = 0;
 
	 /**
	  * @brief Assigns a user as host and grants privileges to manage assets after the meeting.
	  * @param userid The ID of user to be assigned as host.
	  * @param list A List of \link GrantCoOwnerAssetsInfo \endlink struct representing the assets and privileges to grant.
	  * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	  * @note The host cannot be assigned as host by himself. And the user should have the power to assign the role.
	  */
	 virtual SDKError MakeHostWithAssetsPrivilege(unsigned int userid, IList<GrantCoOwnerAssetsInfo>* list) = 0; 

};
END_ZOOM_SDK_NAMESPACE
#endif