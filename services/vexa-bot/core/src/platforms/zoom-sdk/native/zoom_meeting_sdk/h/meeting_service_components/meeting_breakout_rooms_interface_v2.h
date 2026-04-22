/**
 * @file meeting_breakout_rooms_interface2.h
 * @brief Meeting Service Breakout Room Interface
 * Valid for both ZOOM style and user custom interface mode.
 *
 *	//////////////////////////// Creator ////////////////////////////
 *	1. Main Functions:
 *		1) create|delete|rename BO
 *		2) assign|remove user to BO
 *       3) set BO option
 *	2. Remarks:
 *       1) These editing can only be done before BO is started
 *
 *	//////////////////////////// Admin ////////////////////////////
 *   1. Main Functions:
 *		1) after BO is started, assign new user to BO,
 *		2) after BO is started, switch user from BO-A to BO-B
 *       3) stop BO
 *		4) start BO
 *
 *	//////////////////////////// Assistant ////////////////////////////
 *	1. Main Functions:
 *		1) join BO with BO id
 *		2) leave BO
 *
 *   //////////////////////////// Attendee ////////////////////////////
 *   1. Main Functions:
 *		1) join BO
 *       2) leave BO
 *       3) request help
 *
 *	//////////////////////////// DataHelper ////////////////////////////
 *	1. Main Functions:
 *		1) get unassigned user list
 *		2) get BO list
 *       3) get BO object
 *
 *
 *	host in master conference     : creator + admin + assistant + dataHelper
 *	host in BO conference         : admin + assistant + dataHelper
 *	CoHost in master conference   : [attendee] or [creator + admin + assistant + dataHelper]
 *	CoHost in BO conference       : [attendee] or [admin + assistant + dataHelper]
 *	attendee in master conference : attendee + [assistant + dataHelper]
 *   attendee in BO conference     : attendee + [assistant + dataHelper]
 *   
 *   Import Remarks: 
 *   1. attendee in master conference/attendee in BO conference
 *	   1) if BOOption.IsParticipantCanChooseBO is true, attendee has objects:  [attendee + assistant + dataHelper]
 *      2) if BOOption.IsParticipantCanChooseBO is false, attendee has object:  [attendee]
 *   2. CoHost in master conference
 *	   1) if CoHost is desktop client, and host is desktop client, the CoHost has objects: [creator + admin + assistant + dataHelper]
 *      2) if CoHost is desktop client, and host is mobile client, the CoHost has object: [attendee]
 *      3) if CoHost is mobile client, the CoHost has object: [attendee]
 */

#ifndef _MEETING_BREAKOUT_ROOMS_INTERFACE2_H_
#define _MEETING_BREAKOUT_ROOMS_INTERFACE2_H_
#include "zoom_sdk_def.h"

#if defined(WIN32)
#include "customized_ui/customized_share_render.h"
#endif

BEGIN_ZOOM_SDK_NAMESPACE

/**
 * @brief Enumeration of breakout room user control status.
 */
typedef enum
{
	/** User is in main conference, not assigned to BO */
	BO_CTRL_USER_STATUS_UNASSIGNED			= 1, 
	/** User is assigned to BO, but not join */
	BO_CTRL_USER_STATUS_ASSIGNED_NOT_JOIN   = 2, 
	/** User is already in BO */
	BO_CTRL_USER_STATUS_IN_BO				= 3, 
	/** Unknown status */
	BO_CTRL_USER_STATUS_UNKNOWN             = 4, 
}BO_CTRL_USER_STATUS;

typedef enum
{
	/** host receive the help request and there is no other one currently requesting for help */
	ATTENDEE_REQUEST_FOR_HELP_RESULT_IDLE,	 
	/** host is handling other's request with the request dialog */
	ATTENDEE_REQUEST_FOR_HELP_RESULT_BUSY,
	/** host click "later" button or close the request dialog directly */
	ATTENDEE_REQUEST_FOR_HELP_RESULT_IGNORE,
	/** host already in your BO meeting */
	ATTENDEE_REQUEST_FOR_HELP_RESULT_HOST_ALREADY_IN_BO	
}ATTENDEE_REQUEST_FOR_HELP_RESULT;

/**
 * @class IBOMeeting
 * @brief BO interface.
 */
class IBOMeeting
{
public:
	virtual ~IBOMeeting() {}
	/**
	 * @brief Gets the BO ID.
	 * @return If the function succeeds, it returns the BO ID. Otherwise, this function fails and returns nullptr.
	 */
	virtual const zchar_t* GetBOID() = 0;

	/**
	 * @brief Gets the BO name.
	 * @return If the function succeeds, it returns the BO name. Otherwise, this function fails and returns nullptr.
	 */
	virtual const zchar_t* GetBOName() = 0;

	/**
	 * @brief Gets the user ID list in the BO.
	 * @return If the function succeeds, it returns a pointer to IList object. Otherwise, this function fails and returns nullptr.
	 */
	virtual IList<const zchar_t*>* GetBOUserList() = 0;

	/**
	 * @brief Gets the user status by user ID. 
	 * @param strUserID The user's ID.
	 * @return If the function succeeds, it returns the user status.
	 */
	virtual BO_CTRL_USER_STATUS GetBOUserStatus(const zchar_t* strUserID) = 0;
};

////////////////////////////////////////// IBOCreator //////////////////////////////////////////
/**
 * @brief Enumeration of BO creator callback handler.
 */
enum PreAssignBODataStatus
{
	/** initial status, no request was sent */
	PreAssignBODataStatus_none,  
	/** download in progress */
	PreAssignBODataStatus_downloading, 
	/** download success */
	PreAssignBODataStatus_download_ok, 
	/** download fail */
	PreAssignBODataStatus_download_fail      
};

struct BOOption;
class IBOCreatorEvent 
{
public:
	virtual ~IBOCreatorEvent() {}

	/**
	 * @brief Callback event when creating a BO successfully. You will receive this event after CreateBO succeeds. Make sure you receive this event before starting the BO.
	 * @param strBOID The ID of the BO that has been created successfully.
	 * @deprecated This interface is marked as deprecated, and it is recommended to use 'onCreateBOResponse(bool bSuccess, const zchar_t* strBOID)'.
	 */
	virtual void onBOCreateSuccess(const zchar_t* strBOID) = 0;

	/**
	 * @brief Callback event when the pre-assigned data download status changes.
	 * @param status The download status.
	 */
	virtual void OnWebPreAssignBODataDownloadStatusChanged(PreAssignBODataStatus status) = 0;

	/**
	 * @brief Callback event when the BO option changes.
	 * @param newOption The new BO option.
	 */
	virtual void OnBOOptionChanged(const BOOption& newOption) = 0;

	/**
	 * @brief Callback event of CreateBreakoutRoom.
	 * @param bSuccess true if the creation is successful, false otherwise.
	 * @param strBOID The breakout room's ID if the creation is successful, otherwise nullptr.
	 */
	virtual void onCreateBOResponse(bool bSuccess, const zchar_t* strBOID) = 0;

	/**
	 * @brief Callback event of RemoveBO.
	 * @param bSuccess true if the removal is successful, false otherwise.
	 * @param strBOID The breakout room's ID being removed.
	 */
	virtual void onRemoveBOResponse(bool bSuccess, const zchar_t* strBOID) = 0;

	/**
	 * @brief Callback event of UpdateBOName.
	 * @param bSuccess true if the update is successful, false otherwise.
	 * @param strBOID The breakout room's ID being updated.
	 */
	virtual void onUpdateBONameResponse(bool bSuccess, const zchar_t* strBOID) = 0;
};

/**
 * @brief Enumeration of BO stop countdown.
 */
enum BO_STOP_COUNTDOWN
{
	BO_STOP_NOT_COUNTDOWN,
	BO_STOP_COUNTDOWN_SECONDS_10,
	BO_STOP_COUNTDOWN_SECONDS_15,
	BO_STOP_COUNTDOWN_SECONDS_30,
	BO_STOP_COUNTDOWN_SECONDS_60,
	BO_STOP_COUNTDOWN_SECONDS_120,
};

/**
 * @brief BO option.
 */
struct BOOption
{
	/** Set the countdown after closing breakout room. */
	BO_STOP_COUNTDOWN countdown_seconds; 
	/** Enable/Disable that participant can choose breakout room. Only for Meeting not Webinar. */
	bool IsParticipantCanChooseBO;    
	/** Enable/Disable that participant can return to main session at any time. */
	bool IsParticipantCanReturnToMainSessionAtAnyTime; 
	/** Enable/Disable that auto move all assigned participants to breakout room. */
	bool IsAutoMoveAllAssignedParticipantsEnabled;   
	/** true: it's timer BO false: not timer BO */
	bool IsBOTimerEnabled;  
	/** true: if time is up, will stop BO auto. false: don't auto stop. Only for Meeting not Webinar. */
	bool IsTimerAutoStopBOEnabled; 
	/** minutes of BO timer duration, NOTE: when nTimerDurationMinutes is 0, it means that the BO duration is 30 minutes. */
	unsigned int nTimerDurationMinutes;   

	/**
	 * @brief The following items are for Webinar only
	 */
	/** Enable/Disable Webinar Attendee join Webinar BO, When it changes, the BO data will be reset. */
	bool IsAttendeeContained;
	/** Enable/Disable that Panelist can choose breakout room. */
	bool IsPanelistCanChooseBO;	
	/** Enable/Disable that Attendee can choose breakout room, invalid when attendee is not contained. */
	bool IsAttendeeCanChooseBO;	
	/** Enable/Disable that max roomUser limits in BO room. */
	bool IsUserConfigMaxRoomUserLimitsEnabled;	
	/** numbers of max roomUser limits in BO room. */
	unsigned int  nUserConfigMaxRoomUserLimits;	
	BOOption()
	{
		countdown_seconds = BO_STOP_COUNTDOWN_SECONDS_60;
		IsParticipantCanChooseBO = false;
		IsParticipantCanReturnToMainSessionAtAnyTime = true;
		IsAutoMoveAllAssignedParticipantsEnabled = false;
		IsBOTimerEnabled = false;
		IsTimerAutoStopBOEnabled = false;
		nTimerDurationMinutes = 0;
		IsPanelistCanChooseBO = false;
		IsAttendeeCanChooseBO = false;
		IsUserConfigMaxRoomUserLimitsEnabled = false;
		nUserConfigMaxRoomUserLimits = 20;
		IsAttendeeContained = false;
	}
};

/**
 * @class IBatchCreateBOHelper
 * @brief Batch Creater BO helper interface.
 */
class IBatchCreateBOHelper
{
public:
	/**
	 * @brief Prepare to batch create BO rooms.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 * @note If the function succeeds, all the created BO rooms will be removed. And the prepared list you added by calling \link AddNewBoToList \endlink will be clear.
	 */
	virtual SDKError CreateBOTransactionBegin() = 0;

	/**
	 * @brief Add the BO name to a prepared list.
	 * @param strNewBOName, the BO name you want to create.
	 * @return true if the BO name is added to the prepared list successfully.
	 * @note The max number of the prepared list is 50. The max length of the BO room name is 32. CreateBOTransactionBegin() must be called before this function is called. Otherwise false will be returned.
	 */
	virtual bool AddNewBoToList(const zchar_t* strNewBOName) = 0;

	/**
	 * @brief Batch create BO rooms according to the prepare list.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 * @note CreateBOTransactionBegin() must be called before this function is called. Otherwise SDKErr_WRONG_USAGE will be returned.
	 */
	virtual SDKError CreateBoTransactionCommit() = 0;
};

/**
 * @class IBOCreator
 * @brief BO creator interface.
 */
class IBOCreator
{
public:

	virtual void SetEvent(IBOCreatorEvent* pEvent) = 0;

	/**
	 * @brief Create a BO.
	 * @param strBOName, the BO name.
	 * @return if success the return value is BO ID, otherwise nullptr.
	 * @deprecated This interface is marked as deprecated, and it is recommended to use 'CreateBreakoutRoom(const zchar_t* strBOName)'.
	 */
	virtual const zchar_t* CreateBO(const zchar_t* strBOName) = 0;
	
	/**
	 * @brief Create a breakout room.
	 * @param strBOName, the breakout room name.
	 * @return true if the function succeeds. Otherwise, false.
	 * @note
	 * 1. This function is compatible with meeting breakout room and webinar breakout room.
	 * 2. This function is asynchronous. onCreateBOResponse is the corresponding callback notification.
	 * 3. Webinar breakout room only support Zoomui Mode
	 */
	virtual bool CreateBreakoutRoom(const zchar_t* strBOName) = 0;

	/**
	 * @brief Update BO name, 'IBOCreatorEvent.onUpdateBONameResponse' is the corresponding callback notification.
	 * @param strBOID, is the breakout room's ID.
	 * @param strNewBOName, is the new breakout room's name.
	 * @return true if the function succeeds. Otherwise, false.
	 */
	virtual bool UpdateBOName(const zchar_t* strBOID, const zchar_t* strNewBOName) = 0; 
	
	/**
	 * @brief Remove a breakout room, 'IBOCreatorEvent.onRemoveBOResponse' is the corresponding callback notification.
	 * @param strBOID, is the breakout room ID.
	 * @return true if the function succeeds. Otherwise, false.
	 */
	virtual bool RemoveBO(const zchar_t* strBOID) = 0;
	
	/**
	 * @brief Assign a user to a BO.
	 * @param strUserID, is the user ID.
	 * @param strBOID, is the BO ID.
	 * @return true if the function succeeds. Otherwise, false.
	 */
	virtual bool AssignUserToBO(const zchar_t* strUserID, const zchar_t* strBOID) = 0;
	
	/**
	 * @brief Remove some user from a BO.
	 * @param strUserID, is the user ID.
	 * @param strBOID, is the BO ID.
	 * @return true if the function succeeds. Otherwise, false.
	 */
	virtual bool RemoveUserFromBO(const zchar_t* strUserID, const zchar_t* strBOID) = 0;									

	/**
	 * @brief Set BO option.
	 * @param option, the option that you want to set.
	 * @return true if the function succeeds. Otherwise, false.
	 */
	virtual bool SetBOOption(const BOOption& option) = 0;
	
	/**
	 * @brief Get BO option
	 * @param option, Get the current bo option through this parameter.
	 * @return true if the function succeeds. Otherwise, false.
	 */
	virtual bool GetBOOption(BOOption& option) = 0;

	/**
	 * @brief Gets the Batch create bo controller.
	 * @return If the function succeeds, the return value is a pointer to IBatchCreateBOHelper. Otherwise returns nullptr.
	 */
	virtual IBatchCreateBOHelper* GetBatchCreateBOHelper() = 0;

	/**
	 * @brief Determines whether web enabled the pre-assigned option when scheduling a meeting.
	 * @return true if it is enabled. Otherwise, false.
	 */
	virtual bool IsWebPreAssignBOEnabled() = 0;

	/**
	 * @brief Request web pre-assigned data and create those rooms.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError RequestAndUseWebPreAssignBOList() = 0;

	/**
	 * @brief Gets the pre-assigned data download status.
	 * @return The return value is a enum for download status.
	 */
	virtual PreAssignBODataStatus GetWebPreAssignBODataStatus() = 0;

	/**
	 * @brief Create a Webinar BO, Available Only For Zoomui Mode.
	 * @param strBOName, the BO name.
	 * @return true if the function succeeds. Otherwise, false.
	 * @deprecated This interface is marked as deprecated, and it is recommended to use 'CreateBreakoutRoom(const zchar_t* strBOName)'.
	 */
	virtual bool CreateWebinarBo(const zchar_t* strBOName) = 0;
};

////////////////////////////////////////// IBOAdmin //////////////////////////////////////////
enum BOControllerError
{
	BOControllerError_NULL_POINTER = 0,
	BOControllerError_WRONG_CURRENT_STATUS,
	BOControllerError_TOKEN_NOT_READY,
	BOControllerError_NO_PRIVILEGE,
	BOControllerError_BO_LIST_IS_UPLOADING,
	BOControllerError_UPLOAD_FAIL,
	BOControllerError_NO_ONE_HAS_BEEN_ASSIGNED,
	BOControllerError_UNKNOWN = 100
};

/**
 * @class IBOAdminEvent
 * @brief BO admin callback handler.
 */
class IBOAdminEvent
{
public:

	virtual ~IBOAdminEvent() {}

	/**
	 * @brief when someone send the request help, notify it.
	 * @param strUserID, is the user ID which send the request help.
	 */
	virtual void onHelpRequestReceived(const zchar_t* strUserID) = 0;

	/**
	 * @brief when StartBO fail, you will receive the event.
	 * @param errCode, identify the specific error code for trouble shooting.
	 */
	virtual void onStartBOError(BOControllerError errCode) = 0;

	/**
	 * @brief if it's timer BO, after start BO, you will receive the event.
	 * @param [remaining] remaining time, [isTimesUpNotice] true: when time is up, auto stop BO. false: don't auto stop BO.
	 */
	virtual void onBOEndTimerUpdated(int remaining, bool isTimesUpNotice) = 0;

	/**
	 * @brief The callback notification of StartBO.
	 * @param bSuccess, Indicates whether the startup is actually successful. true indicates success, and false indicates failure.
	 */
	virtual void onStartBOResponse(bool bSuccess) = 0;

	/**
	 * @brief The callback notification of StopBO.
	 * @param bSuccess, Indicates whether the stop is actually successful. true indicates success, and false indicates failure.
	 */
	virtual void onStopBOResponse(bool bSuccess) = 0;
};

/**
 * @class IBOAdmin
 * @brief BO admin interface.
 */
class IBOAdmin
{
public:
	/**
	 * @brief start breakout room, 'IBOAdminEvent.onStartBOResponse' is the corresponding callback notification.
	 * @return true if the function succeeds. Otherwise, false.
	 */
	virtual bool StartBO() = 0;

	/**
	 * @brief stop breakout room, 'IBOAdminEvent.onStopBOResponse' is the corresponding callback notification.
	 * @return true if the function succeeds. Otherwise, false.
	 */
	virtual bool StopBO() = 0;
	
	/**
	 * @brief To set a unassigned user to a BO, when BO is started.
	 * @return true if the function succeeds. Otherwise, false.
	 */
	virtual bool AssignNewUserToRunningBO(const zchar_t* strUserID, const zchar_t* strBOID) = 0;
	
	/**
	 * @brief To Switch user to other BO, when BO is started.
	 * @return true if the function succeeds. Otherwise, false.
	 */
	virtual bool SwitchAssignedUserToRunningBO(const zchar_t* strUserID, const zchar_t* strBOID) = 0;
	
	/**
	 * @brief Determines if can start BO.
	 * @return true if can start BO. Otherwise, false.
	 */
	virtual bool CanStartBO() = 0;
	
	/**
	 * @brief Sets admin callback handler.
	 * @param pEvent, A pointer to the IBOAdminEvent.
	 */
	virtual void SetEvent(IBOAdminEvent* pEvent) = 0;
	
	/**
	 * @brief To join the BO which request help is from.
	 * @return true if the function succeeds. Otherwise, false.
	 */
	virtual bool JoinBOByUserRequest(const zchar_t* strUserID) = 0;
	
	/**
	 * @brief To ignore the request help.
	 * @return true if the function succeeds. Otherwise, false.
	 */
	virtual bool IgnoreUserHelpRequest(const zchar_t* strUserID) = 0;

	/**
	 * @brief To send the broadcast message.
	 * @return true if the function succeeds. Otherwise, false.
	 */
	virtual bool BroadcastMessage(const zchar_t* strMsg) = 0;

	/**
	 * @brief Host invite user return to main session, When BO is started and user is in BO.
	 * @return true if the function succeeds. Otherwise, false.
	 */
	virtual bool InviteBOUserReturnToMainSession(const zchar_t* strUserID) = 0;

	/**
	 * @brief Query if the current meeting supports broadcasting host's voice to BO.
	 * @return true if the meeting supports broadcasting. Otherwise, false.
	 */
	virtual bool IsBroadcastVoiceToBOSupport() = 0;

	/**
	 * @brief Query if the host now has the ability to broadcast voice to BO.
	 * @return true if the host has the ability to broadcast voice to BO. Otherwise, false.
	 */
	virtual bool CanBroadcastVoiceToBO() = 0;

	/**
	 * @brief Starts or stop broadcasting voice to BO.
	 * @param bStart true for start and false for stop.
	 * @return true if the invocation succeeds. Otherwise, false.
	 */
	virtual bool BroadcastVoiceToBo(bool bStart) = 0;
};

////////////////////////////////////////// IBOAssistant //////////////////////////////////////////

/**
 * @class IBOAssistant
 * @brief BO assistant interface.
 */
class IBOAssistant
{
public:
	/**
	 * @brief Join BO by BO ID.
	 * @return true if the function succeeds. Otherwise, false.
	 */
	virtual bool JoinBO(const zchar_t* strBOID) = 0;
	
	/**
	 * @brief leave BO
	 * @return true if the function succeeds. Otherwise, false.
	 */
	virtual bool LeaveBO() = 0;	
};

////////////////////////////////////////// IBOAttendee //////////////////////////////////////////
/**
 * @class IBOAttendeeEvent
 * @brief attendee callback handler.
 */
class IBOAttendeeEvent
{
public:

	virtual ~IBOAttendeeEvent() {}

	/**
	 * @brief To notify the status of request help.
	 * @param eResult.
	 */
	virtual void onHelpRequestHandleResultReceived(ATTENDEE_REQUEST_FOR_HELP_RESULT eResult) = 0;

	/**
	 * @brief To notify if host has joined the BO.
	 */
	virtual void onHostJoinedThisBOMeeting() = 0;

	/**
	 * @brief To notify if host has leaved the BO.
	 */
	virtual void onHostLeaveThisBOMeeting() = 0;
};
/**
 * @class IBOAttendee
 * @brief attendee interface
 */
class IBOAttendee
{
public:
	/**
	 * @brief Join BO for attendee which is assigned to a BO.
	 * @return true if the function succeeds. Otherwise, false.
	 */
	virtual bool JoinBo() = 0;

	/**
	 * @brief Leave BO for attendee which is in a BO.
	 * @return true if the function succeeds. Otherwise, false.
	 */
	virtual bool LeaveBo() = 0;

	/**
	 * @brief Gets name of the BO that attendee is assigned to.
	 */
	virtual const zchar_t* GetBoName() = 0;

	/**
	 * @brief Sets attendee callback handler.
	 * @param pEvent, A pointer to the IBOAttendeeEvent.
	 */
	virtual void SetEvent(IBOAttendeeEvent* pEvent) = 0;

	/**
	 * @brief Request help for attendee.
	 * @return true if the function succeeds. Otherwise, false.
	 */
	virtual bool RequestForHelp() = 0;

	/**
	 * @brief Determines if host is in the BO which attendee is assigned to.
	 * @return true if host is in. Otherwise, false.
	 */
	virtual bool IsHostInThisBO() = 0;

	/**
	 * @brief Determines if participant can return to main session.
	 * @return true if participant can return to main session. Otherwise, false.
	 */
	virtual bool IsCanReturnMainSession() = 0;
};

////////////////////////////////////////// IBOData //////////////////////////////////////////
/**
 * @class IBODataEvent
 * @brief BO data callback handler.
 */
class IBODataEvent
{
public:
	virtual ~IBODataEvent() {}

	/**
	 * @brief To notify if some BO information is changed(user join/leave BO or BO user name is modified)
	 * @param strBOID, the BO ID which information is changed.
	 */
	virtual void onBOInfoUpdated(const zchar_t* strBOID) = 0; 
	
	/**
	 * @brief To notify if unassigned user join/leave master conference or name is modified.
	 * @note Once you receive the callback, you need call GetUnassignedUserList to update the unassigned user information.
	 */
	virtual void onUnAssignedUserUpdated() = 0; 

	/**
	 * @brief Host/CoHost both can edit BO, Host edit BO->start BO->stop BO, then CoHost edit BO->start BO, you will receive the event, you must update BO list in UI.
	 */
	virtual void OnBOListInfoUpdated() = 0;
};
/**
 * @class IBOData
 * @brief BO data interface
 */
class IBOData
{
public:
	/**
	 * @brief Set BO data callback handler.
	 * @param pEvent, A pointer to the IBODataEvent.
	 */
	virtual void SetEvent(IBODataEvent* pEvent) = 0;

	/**
	 * @brief Gets the id list of all unassigned users. 
	 * @return If the function succeeds, the return value is a pointer to IList object. Otherwise, the return value is nullptr.
	 */
	virtual IList<const zchar_t*>* GetUnassignedUserList() = 0;

	/**
	 * @brief Gets the id list of all BOs.
	 * @return If the function succeeds, the return value is a pointer to IList object. Otherwise, the return value is nullptr.
	 */
	virtual IList<const zchar_t*>* GetBOMeetingIDList() = 0;
	
	/**
	 * @brief Gets user name by user ID. 
	 * @return user name
	 */
	virtual const zchar_t* GetBOUserName(const zchar_t* strUserID) = 0;

	/**
	 * @brief Determines if strUserID is myself.
	 * @return true if strUserID is myself. Otherwise, false.
	 */
	virtual bool IsBOUserMyself(const zchar_t* strUserID) = 0;

	/**
	 * @brief Get BO object by BO ID.
	 * @return If the function succeeds, the return value is a pointer to IBOMeeting object. Otherwise, the return value is nullptr.
	 */
	virtual IBOMeeting* GetBOMeetingByID(const zchar_t* strBOID) = 0;

	/**
	 * @brief Gets current BO name if you in a BO.
	 * @return BO name
	 */
	virtual const zchar_t* GetCurrentBoName() = 0;
};

////////////////////////////////////////// IMeetingBOController //////////////////////////////////////////

/**
 * @brief Enumeration of BO status.
 */
enum BO_STATUS
{
	/** invalid */
	BO_STATUS_INVALID = 0, 
	/** edit & assign	 */
	BO_STATUS_EDIT = 1,	 
	/** BO is started	 */
	BO_STATUS_STARTED = 2,
	/** stopping BO	 */
	BO_STATUS_STOPPING = 3,	
	/** BO is ended */
	BO_STATUS_ENDED = 4		
};

/**
 * @class IReturnToMainSessionHandler
 * @brief handler for return to main session.
 */
class IReturnToMainSessionHandler
{
public:
	virtual ~IReturnToMainSessionHandler() {}

	/**
	 * @brief return to main session.
	 * @return true if the call is successful. Otherwise, false.
	 */
	virtual bool ReturnToMainSession() = 0;

	/**
	 * @brief Ignore the return invitation, after call 'Ignore()', please don't use the handler unless you receive the invitation again.
	 */
	virtual void Ignore() = 0;
};

/**
 * @class IMeetingBOControllerEvent
 * @brief BO controller callback event handler.
 */
class IMeetingBOControllerEvent
{
public:
	virtual ~IMeetingBOControllerEvent() {}

	/**
	 * @brief To notify that you has creator right. 
	 * @param pCreatorObj, the pointer of creator object.
	 */
	virtual void onHasCreatorRightsNotification(IBOCreator* pCreatorObj) = 0;

	/**
	 * @brief To notify that you has admin right. 
	 * @param pAdminObj, the pointer of admin object.
	 */
	virtual void onHasAdminRightsNotification(IBOAdmin* pAdminObj) = 0;

	/**
	 * @brief To notify that you has assistant right. 
	 * @param pAssistantObj, the pointer of assistant object.
	 */
	virtual void onHasAssistantRightsNotification(IBOAssistant* pAssistantObj) = 0;

	/**
	 * @brief To notify that you has assistant right.
	 * @param pAttendeeObj, the pointer of attendee object.
	 */
	virtual void onHasAttendeeRightsNotification(IBOAttendee* pAttendeeObj) = 0;

	/**
	 * @brief To notify that you has data right. 
	 * @param pDataHelperObj, the pointer of data helper object.
	 */
	virtual void onHasDataHelperRightsNotification(IBOData* pDataHelperObj) = 0;

	/**
	 * @brief To notify that you lost creator right. 
	 */ 
	virtual void onLostCreatorRightsNotification() = 0;
	
	/**
	 * @brief To notify that you lost admin right. 
	 */
	virtual void onLostAdminRightsNotification() = 0;

	/**
	 * @brief To notify that you lost assistant right. 
	 */ 
	virtual void onLostAssistantRightsNotification() = 0;

	/**
	 * @brief To notify that you lost attendee right. 
	 */
	virtual void onLostAttendeeRightsNotification() = 0;

	/**
	 * @brief To notify that you lost attendee right.
	 */ 
	virtual void onLostDataHelperRightsNotification() = 0;

	/**
	 * @brief To notify that you receive a broadcast message. 
	 * @param strMsg, the message content.
	 * @param nSenderID, the SenderID.
	 */
	virtual void onNewBroadcastMessageReceived(const zchar_t* strMsg, unsigned int nSenderID, const zchar_t* strSenderName) = 0;

	/**
	 * @brief When BOOption.countdown_seconds != BO_STOP_NOT_COUNTDOWN, host stop BO and all users receive the event.
	 * @param nSeconds, the countdown seconds.
	 */
	virtual void onBOStopCountDown(unsigned int nSeconds) = 0;

	/**
	 * @brief When you are in BO, host invite you return to main session, you will receive the event.
	 * @param strName, the host name.
	 */
	virtual void onHostInviteReturnToMainSession(const zchar_t* strName, IReturnToMainSessionHandler* handler) = 0;

	/**
	 * @brief When host change the BO status, all users receive the event.
	 * @param eStatus, current status of BO.
	 */
	virtual void onBOStatusChanged(BO_STATUS eStatus) = 0; 

	/**
	 * @brief Whenever the host switches you to another BO while you are assigned but haven't joined the BO, you will receive this event.
	 * @param strNewBOName The new BO name.
	 * @param strNewBOID The new BO ID. If the current user is IBOAttendee, then the 2nd parameter strNewBOID will return nullptr.
	 */
	virtual void onBOSwitchRequestReceived(const zchar_t* strNewBOName, const zchar_t* strNewBOID) = 0;

	/**
	 * @brief The status of broadcasting voice to BO has been changed.
	 * @param bStart true for start and false for stop.
	 */
	virtual void onBroadcastBOVoiceStatus(bool bStart) = 0;
#if defined(WIN32)
	/**
	 * @brief You will receive this event when you are in a breakout room and someone shares from the main session to the breakout room.
	 * @param iSharingID The sharing ID.
	 * @param status The sharing status.
     * @param pShareAction The pointer of share action object.
	 * @note Valid for user custom interface mode only.
	 */
	virtual void onShareFromMainSession(const unsigned int iSharingID, SharingStatus status, IShareAction* pShareAction) = 0;
#endif
};

/**
 * @class IMeetingBOController
 * @brief Meeting breakout rooms controller interface
 */
class IMeetingBOController
{
public:
	/**
	 * @brief Sets breakout room callback event handler.
	 * @param event, A pointer to the IMeetingBOControllerEvent.
	 * @return true if the function succeeds. Otherwise, false.
	 */
	virtual bool SetEvent(IMeetingBOControllerEvent* event) = 0;

	/**
	 * @brief Gets the pointer of BO creator object. 
	 * @return If the function succeeds, the return value is a pointer to IBOCreator object. Otherwise, the return value is nullptr.
	 */
	virtual IBOCreator*    GetBOCreatorHelper() = 0;

	/**
	 * @brief Gets the pointer of BO administrator object. 
	 * @return If the function succeeds, the return value is a pointer to IBOAdmin object. Otherwise, the return value is nullptr.
	 */
	virtual IBOAdmin*      GetBOAdminHelper() = 0;

	/**
	 * @brief Gets the pointer of BO assistant object. 
	 * @return If the function succeeds, the return value is a pointer to IBOAssistant object. Otherwise, the return value is nullptr.
	 */
	virtual IBOAssistant*  GetBOAssistantHelper() = 0;

	/**
	 * @brief Gets the pointer of BO attendee object. 
	 * @return If the function succeeds, the return value is a pointer to IBOAttendee object. Otherwise, the return value is nullptr.
	 */
	virtual IBOAttendee*   GetBOAttedeeHelper() = 0;

	/**
	 * @brief Gets the pointer of BO data object. 
	 * @return If the function succeeds, the return value is a pointer to IBOData object. Otherwise, the return value is nullptr.
	 */
	virtual IBOData*	   GetBODataHelper() = 0;

	/**
	 * @brief Determines if the BO is started or not.
	 * @return true if the host has started the BO. Otherwise, false.
	 */
	virtual bool IsBOStarted() = 0;
	
	/**
	 * @brief Determines if the BO feature is enabled in current meeting.
	 * @return true indicates that BO feature is enabled in current meeting.
	 */
	virtual bool IsBOEnabled() = 0;

	/**
	 * @brief Determines if myself is in BO meeting.
	 * @return true indicates that i am in a BO meeting.
	 */
	virtual bool IsInBOMeeting() = 0;

	/**
	 * @brief Gets current BO status
	 * @return The return value is a enum for bo status.
	 */
	virtual BO_STATUS GetBOStatus() = 0;

	/**
	 * @brief Query if the host is broadcasting voice to BO.
	 * @return true if the host is broadcasting voice to BO. Otherwise, false.
	 */
	virtual bool IsBroadcastingVoiceToBO() = 0;

	/**
	 * @brief Gets the name of the BO you are going to. When you enter a BO or are switched to another BO by the host, maybe you need the BO name to display on transfer UI.
	 */
	virtual const zchar_t* GetJoiningBOName() = 0;
};

END_ZOOM_SDK_NAMESPACE
#endif