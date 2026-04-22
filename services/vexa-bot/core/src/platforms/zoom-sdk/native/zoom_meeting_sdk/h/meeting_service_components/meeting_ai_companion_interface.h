/**
 * @file meeting_ai_companion_interface.h
 * @brief Meeting Service AI Companion Interface.
 */
#ifndef _MEETING_AI_COMPANION_INTERFACE_H_
#define _MEETING_AI_COMPANION_INTERFACE_H_
#include "zoom_sdk_def.h"

BEGIN_ZOOM_SDK_NAMESPACE

/**
 * @brief Enumeration of AI companion feature.
 */
enum AICompanionFeature
{
	/** Meeting summary with AI Companion generates summary assets. */
	SMART_SUMMARY,    
	/** Meeting questions with AI Companion generates transcript assets. */
	QUERY,            
	/** Smart recording with AI Companion generates recording assets. */
	SMART_RECORDING,  
};

/**
 * @brief Enumeration of feedback types for AI Companion queries in a meeting.
 */
enum MeetingAICompanionQueryFeedbackType
{
	/** No feedback provided. */
	MeetingAICompanionQueryFeedbackType_None = 0,	
	/** The feedback indicates the query response was helpful or satisfactory. */
	MeetingAICompanionQueryFeedbackType_Good,		
	/** The feedback indicates the query response was unhelpful or unsatisfactory. */
	MeetingAICompanionQueryFeedbackType_Bad
};

/**
 * @brief Enumeration of possible error codes when requesting an AI Companion query during a meeting.
 */
enum MeetingAICompanionQueryRequestError
{
	/** Request succeeded with no error. */
	MeetingAICompanionQueryRequestError_OK = 0,					
	/** The provided parameter(s) are invalid. */
	MeetingAICompanionQueryRequestError_InvalidParam,			
	/** Failed to send the query request. */
	MeetingAICompanionQueryRequestError_SendFailed,				
	/** The AI Companion web service is unavailable. */
	MeetingAICompanionQueryRequestError_WebUnAvailable,			
	/** Permission verification failed for the request. */
	MeetingAICompanionQueryRequestError_PermissionVerifyFailed,
	/** The query request was rate-limited due to excessive frequency. */
	MeetingAICompanionQueryRequestError_QueryRateLimitError,	
	/** The query request timed out. */
	MeetingAICompanionQueryRequestError_Timeout,				
	/** An unknown error occurred. */
	MeetingAICompanionQueryRequestError_Unknown = 100			
};

/**
 * @brief Enumeration of config who in the meeting can interact with AI Companion by asking questions about the meeting. please visit <https://support.zoom.com/hc/en/article?id=zm_kb&sysparm_article=KB0057749>
 */
enum MeetingAICompanionQuerySettingOptions
{
	/** Initialization. */
	MeetingAICompanionQuerySettingOptions_None = 0,				
	/** Allows all participants and attendee to ask questions about all discussions since AI Companion was active. */
	MeetingAICompanionQuerySettingOptions_WhenQueryStarted,		
	/** Allows all participants to ask questions about the discussion since they joined the current meeting. */
	MeetingAICompanionQuerySettingOptions_WhenParticipantsJoin,	
	/** Allow only the host, alternative host, and users with host privileges assigned before the meeting starts to ask questions about all discussions since AI Companion is active. */
	MeetingAICompanionQuerySettingOptions_OnlyHost,				
	/** Allows only participants and invitees from your organization to ask questions about all discussions since AI Companion was active. */
	MeetingAICompanionQuerySettingOptions_ParticipantsAndInviteesInOurOrganization, 
	/** Allows only participants from your organization to ask questions about the discussion since they joined the current meeting. */
	MeetingAICompanionQuerySettingOptions_WhenParticipantsAndOrganizationJoin,        
	/** Hosts and all panelists can ask question, and answers are based on the meeting's start until now. */
	MeetingAICompanionQuerySettingOptions_HostAndPanelists = 10,  
	/** Hosts and all panelists in our organization can ask question, and answers are based on the meeting's start until now. */
	MeetingAICompanionQuerySettingOptions_HostAndPanelistsInOurOrganization    
};

/**
 * @class IMeetingEnableSmartSummaryHandler
 * @brief The handler to enable smart summary.
 */
class IMeetingEnableSmartSummaryHandler
{
public:
	virtual ~IMeetingEnableSmartSummaryHandler() {};

    /**
	 * @brief Enables smart summary.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError EnableSmartSummary() = 0;

    /**
	 * @brief Determines if this handler is requesting enable smart summary.
	 * @return true if this handler is for requesting to enable smart summary. Otherwise, false.
	 */
	virtual bool IsForRequest() = 0;
};

/**
 * @class IMeetingStartSmartSummaryHandler
 * @brief The handler to start the smart summary.
 */
class IMeetingStartSmartSummaryHandler
{
public:
	virtual ~IMeetingStartSmartSummaryHandler() {};

    /**
	 * @brief Starts smart summary.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError StartSmartSummary() = 0;

    /**
	 * @brief Determines if this handler is for requesting to start the smart summary.
	 * @return true if this handler is requesting to start the smart summary. Otherwise, false.
	 */
	virtual bool IsForRequest() = 0;
};

/**
 * @class IMeetingStopSmartSummaryHandler
 * @brief The handler to handle stopping the smart summary.
 */
class IMeetingStopSmartSummaryHandler
{
public:
	virtual ~IMeetingStopSmartSummaryHandler() {};

    /**
	 * @brief Stops smart summary.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError StopSmartSummary() = 0;
};

/**
 * @class IMeetingEnableSmartSummaryActionHandler
 * @brief The handler to confirm enabling smart summary
 */
class IMeetingEnableSmartSummaryActionHandler
{
public:
	virtual ~IMeetingEnableSmartSummaryActionHandler() {};

    /**
	 * @brief Gets the title of tip.
	 * @return The title of tip.
	 */
	virtual const zchar_t* GetTipTitle() = 0;

    /**
	 * @brief Gets the string of tip.
	 * @return The string of tip.
	 */
	virtual const zchar_t* GetTipString() = 0;

    /**
	 * @brief Confirm enabling smart summary.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError Confirm() = 0;

    /**
	 * @brief Cancel enabling smart summary.
     * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError Cancel() = 0;
};

/**
 * @class IMeetingApproveStartSmartSummaryHandler
 * @brief The handler to approve the smart summary started request.
 */
class IMeetingApproveStartSmartSummaryHandler
{
public:
	virtual ~IMeetingApproveStartSmartSummaryHandler() {};

    /**
	 * @brief Gets the user ID of the requester.
	 * @return The user ID of the requester.
	 */
	virtual unsigned int GetSenderUserID() = 0;

    /**
	 * @brief Approve request.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError Approve() = 0;

    /**
	 * @brief Decline request.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError Decline() = 0;
};

/**
 * @class IMeetingApproveEnableSmartSummaryHandler
 * @brief The handler to approve the smart summary enable request.
 */
class IMeetingApproveEnableSmartSummaryHandler
{
public:
	virtual ~IMeetingApproveEnableSmartSummaryHandler() {};

    /**
	 * @brief Gets the user ID of the requester.
	 * @return The user ID of the requester.
	 */
	virtual unsigned int GetSenderUserID() = 0;

    /**
	 * @brief Continue approve action.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError ContinueApprove() = 0;
};

/**
 * @class IMeetingAICompanionSmartSummaryHelperEvent
 * @brief Meeting smart summary callback event.
 */
class IMeetingAICompanionSmartSummaryHelperEvent
{
public:
    /**
	 * @brief Notification of the meting does not support smart summary.
	 */
	virtual void onSmartSummaryStateNotSupported() = 0;

    /**
	 * @brief Notification of the meeting support smart summary but smart summary feature is disabled.
	 * @param handler The handler to enable smart summary.
	 */
	virtual void onSmartSummaryStateSupportedButDisabled(IMeetingEnableSmartSummaryHandler* handler) = 0;

    /**
	 * @brief Notification of the meeting smart summary is not started.
	 * @param handler The handler to start smart summary.
	 */
	virtual void onSmartSummaryStateEnabledButNotStarted(IMeetingStartSmartSummaryHandler* handler) = 0;

    /**
	 * @brief Notification of the meeting smart summary is started.
	 * @param handler The handler to stop smart summary. If the user can not stop smart summary, the handler will be null.
	 */
	virtual void onSmartSummaryStateStarted(IMeetingStopSmartSummaryHandler* handler) = 0;

    /**
	 * @brief Notification of failed to start the smart summary.
	 * @param bTimeout true indicates timeout. Otherwise no timeout. May be declined by host or cohost.
	 */
	virtual void onFailedToStartSmartSummary(bool bTimeout) = 0;

    /**
	 * @brief Notification of receiving request to enable smart summary.
	 * @param handler The handler to handle enable smart summary request.
	 */
	virtual void onSmartSummaryEnableRequestReceived(IMeetingApproveEnableSmartSummaryHandler* handler) = 0;

    /**
	 * @brief Notification of receiving request to start smart summary.
	 * @param handler The handler to handle request.
	 */
	virtual void onSmartSummaryStartRequestReceived(IMeetingApproveStartSmartSummaryHandler* handler) = 0;

    /**
	 * @brief Notification of receiving smart summary enable action.
	 * @param handler The handler to enable smart summary.
	 */
	virtual void onSmartSummaryEnableActionCallback(IMeetingEnableSmartSummaryActionHandler* handler) = 0;

	virtual ~IMeetingAICompanionSmartSummaryHelperEvent() {}

};

/**
 * @class IMeetingAICompanionSmartSummaryHelper
 * @brief Smart summary helper in meeting.
 */
class IMeetingAICompanionSmartSummaryHelper
{
public:
	virtual ~IMeetingAICompanionSmartSummaryHelper() {}

    /**
	 * @brief Sets the smart summary callback event handler.
	 * @param event A pointer to the @ref IMeetingAICompanionSmartSummaryHelperEvent that receives the smart summary event. 
	 */
	virtual void SetEvent(IMeetingAICompanionSmartSummaryHelperEvent* event) = 0;
};


/**
 * @class ISmartSummaryPrivilegeHandler
 * @brief The handler of start smart summary request
 * @deprecated This class is marked as deprecated.
 */
class ISmartSummaryPrivilegeHandler
{
public:
	virtual ~ISmartSummaryPrivilegeHandler() {};

    /**
	 * @brief Agree the start smart summary request.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError Accept() = 0;

    /**
	 * @brief Decline the start smart summary request.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError Decline() = 0;

    /**
	 * @brief Ignore the start smart summary request.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError Ignore() = 0;
};

/**
 * @class IMeetingSmartSummaryHelperEvent
 * @brief Meeting smart summary callback event.
 * @deprecated This class is marked as deprecated.
 */
class IMeetingSmartSummaryHelperEvent
{
public:
    /**
	 * @brief Notification of smart summary status changes.
	 * @param isStarted true indicates smart summary is started, false means not.
	 */
	virtual void onSmartSummaryStatusChange(bool isStarted) = 0;

    /**
	 * @brief Notification of a user requests host to start smart summary.
	 * @param senderId The user who requests host to start smart summary.
	 * @param handler The handler to handle the smart summary start request.
	 */
	virtual void onSmartSummaryPrivilegeRequested(unsigned int senderId, ISmartSummaryPrivilegeHandler* handler) = 0;

    /**
	 * @brief Notification of the host handle the smart summary request.
	 * @param timeout true indicates the host not handle the request until timeout.
	 * @param decline true indicates the host decline the request, false means the host agree the request.
	 */
	virtual void onSmartSummaryStartReqResponse(bool timeout, bool decline) = 0;

    /**
	 * @brief Notification of the user need to go to web to enable the smart summary feature.
	 * @note Valid for both ZOOM style and user custom interface mode. The callback will only be triggered for Business account. And the smart summary feature will be enabled for the future meeting, but not enable the current meeting if the user enables the setting in web.
	 */
	virtual void onEnableSmartSummary() = 0;

	virtual ~IMeetingSmartSummaryHelperEvent() {}

};

/**
 * @class IMeetingSmartSummaryHelper
 * @brief Meeting smart summary helper interface.
 * @deprecated This class is marked as deprecated.
 */
class IMeetingSmartSummaryHelper
{
public:
	virtual ~IMeetingSmartSummaryHelper() {}
    /**
	 * @brief Sets the smart summary callback event handler.
	 * @param event A pointer to the @ref IMeetingSmartSummaryHelperEvent that receives the smart summary event. 
	 */
	virtual void SetEvent(IMeetingSmartSummaryHelperEvent* event) = 0;

    /**
	 * @brief Determines if current meeting support smart summary feature.
	 * @return true if the current meeting supports the smart summary feature. Otherwise, false.
	 */
	virtual bool IsSmartSummarySupported() = 0;

    /**
	 * @brief Determines if smart summary feature is enabled in the meeting.
	 * @return true if the smart summary feature is enabled. Otherwise, false.
	 */
	virtual bool IsSmartSummaryEnabled() = 0;

    /**
	 * @brief Whether the current user can enable the smart summary feature for the account.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError CanEnableSmartSummaryFeature() = 0;

    /**
	 * @brief Enables the smart summary feature for the account.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError EnableSmartSummaryFeature() = 0;

    /**
	 * @brief Whether the current user is able to start smart summary.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError CanStartSmartSummary() = 0;

    /**
	 * @brief Starts smart summary.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError StartSmartSummary() = 0;

    /**
	 * @brief Stops smart summary.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError StopSmartSummary() = 0;

    /**
	 * @brief Query whether smart summary is started.
	 * @return true if smart summary is started. Otherwise, false.
	 */
	virtual bool IsSmartSummaryStarted() = 0;

    /**
	 * @brief Whether the current user can request the admin to enable the smart summary feature for the account.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError CanRequestEnableSmartSummaryFeature() = 0;

    /**
	 * @brief Request the admin to enable the smart summary feature for the account.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError RequestEnableSmartSummaryFeature() = 0;

    /**
	 * @brief Whether the current user can request host to start the smart summary for the current meeting.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError CanRequestStartSmartSummary() = 0;

    /**
	 * @brief Request the host to start the smart summary for the current meeting.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError RequestStartSmartSummary() = 0;
};

/**
 * @class IMeetingAICompanionQueryItem
 * @brief Get AI companion query item information.
 */
class IMeetingAICompanionQueryItem
{
public:
	virtual ~IMeetingAICompanionQueryItem() {}

    /**
	 * @brief Gets the query question ID.
	 */
	virtual const zchar_t* GetQueryID() = 0;

    /**
	 * @brief Gets the query question content.
	 */
	virtual const zchar_t* GetQustionContent() = 0;

    /**
	 * @brief Gets the query answer content.
	 */
	virtual const zchar_t* GetAnswerContent() = 0;

    /**
	 * @brief Gets the error code.
	 */
	virtual MeetingAICompanionQueryRequestError GetErrorCode() = 0;

    /**
	 * @brief Gets the error message.
	 */
	virtual const zchar_t* GetErrorMsg() = 0;

    /**
	 * @brief Gets the timestamp.
	 */
	virtual time_t GetTimeStamp() = 0;

    /**
	 * @brief Sends feedback of query answer.
	 * @param eFeedbackType The feedback type.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError Feedback(MeetingAICompanionQueryFeedbackType eFeedbackType) = 0;
};

/**
 * @class IMeetingEnableQueryHandler
 * @brief The handler to enable AI companion query.
 */
class IMeetingEnableQueryHandler
{
public:
	virtual ~IMeetingEnableQueryHandler() {}

    /**
	 * @brief Enables meeting query.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError EnableQuery() = 0;

    /**
	 * @brief Determines if this handler for request enable query.
	 * @return true if this handler is for requesting to enable query. Otherwise, false.
	 */
	virtual bool IsForRequest() = 0;
};

/**
 * @class IMeetingStartQueryHandler
 * @brief The handler to start AI companion query.
 */
class IMeetingStartQueryHandler
{
public:
	virtual ~IMeetingStartQueryHandler() {}

    /**
	 * @brief Starts meeting query.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError StartMeetingQuery() = 0;

    /**
	 * @brief Determines if this handler for request start query.
	 * @return true if this handler is for requesting to start query. Otherwise, false.
	 */
	virtual bool IsForRequest() = 0;
};

/**
 * @class IMeetingApproveEnableQueryHandler
 * @brief The handler to approve enable AI companion query.
 */
class IMeetingApproveEnableQueryHandler
{
public:
	virtual ~IMeetingApproveEnableQueryHandler() {}

    /**
	 * @brief Gets the user id of requester.
	 * @return The user ID of the requester.
	 */
	virtual unsigned int GetSenderUserID() = 0;

    /**
	 * @brief Continue approve action.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError ContinueApprove() = 0;
};

/**
 * @class IMeetingApproveStartQueryHandler
 * @brief The handler to approve start AI companion query.
 */
class IMeetingApproveStartQueryHandler
{
public:
	virtual ~IMeetingApproveStartQueryHandler() {}

    /**
	 * @brief Gets the user ID of requester.
	 * @return The user ID of the requester.
	 */
	virtual unsigned int GetSenderUserID() = 0;

    /**
	 * @brief Approve the request.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError Approve() = 0;

    /**
	 * @brief Decline the request.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError Decline() = 0;
};

/**
 * @class IMeetingSendQueryHandler
 * @brief The handler to send query.
 */
class IMeetingSendQueryHandler
{
public:
	virtual ~IMeetingSendQueryHandler() {}

    /**
	 * @brief Gets default query questions.
	 * @return If the function succeeds, it returns the array of questions. Otherwise, this function fails and returns nullptr.
	 */
	virtual IList<const zchar_t*>* GetDefaultQueryQuestions() = 0;

    /**
	 * @brief Sends query question.
	 * @param question The query question.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError SendQueryQuestion(const zchar_t* sQuestion) = 0;

    /**
	 * @brief Stops meeting query.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError StopMeetingQuery() = 0;

    /**
	 * @brief Determines if the current user can send query.
	 * @return true if the current user can send query. Otherwise, false.
	 */
	virtual bool CanSendQuery() = 0;

    /**
    * @brief Request send query privilege.
    * @return If the function succeeds, it returns ZoomSDKError_Success Otherwise, this function returns an error.
	 */
	virtual SDKError RequestSendQueryPrivilege() = 0;
};

/**
 * @class IMeetingEnableQueryActionHandler
 * @brief The handler to enable query action.
 */
class IMeetingEnableQueryActionHandler
{
public:
	virtual ~IMeetingEnableQueryActionHandler() {}

    /**
	 * @brief Gets the title of the tip.
	 * @return The title of tip.
	 */
	virtual const zchar_t* GetTipTitle() = 0;

    /**
	 * @brief Gets the tip string.
	 * @return The string of tip.
	 */
	virtual const zchar_t* GetTipString() = 0;

    /**
	 * @brief Confirm enable query. The object will be deleted after this interface call.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError Confirm() = 0;

    /**
	 * @brief Cancel enable query. The object will be deleted after this interface call.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError Cancel() = 0;
};

/**
 * @class IMeetingApproveSendQueryHandler
 * @brief The handler to approve send query.
 */
class IMeetingApproveSendQueryHandler
{
public:
	virtual ~IMeetingApproveSendQueryHandler() {}

    /**
	 * @brief Gets the user ID of requester.
	 * @return The user ID of the requester.
	 */
	virtual unsigned int GetSenderUserID() = 0;

    /**
	 * @brief Approve the request.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError Approve() = 0;

    /**
	 * @brief Decline the request.
	 * @param bDeclineAll true indicates decline all requests.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError Decline(bool bDeclineAll) = 0;
};


/**
 * @class IMeetingAICompanionQueryHelperEvent
 * @brief Meeting AI companion query callback event.
 */
class IMeetingAICompanionQueryHelperEvent
{
public:
	virtual ~IMeetingAICompanionQueryHelperEvent() {}

    /**
	 * @brief Notification of the meeting does not support query.
	 */
	virtual void onQueryStateNotSupported() = 0;

    /**
	 * @brief Notification of the meeting supports query but query feature is disabled.
	 * @param pHandler The handler to enable the query.
	 */
	virtual void onQueryStateSupportedButDisabled(IMeetingEnableQueryHandler* pHandler) = 0;

    /**
	 * @brief Notification of the query is not started.
	 * @param pHandler The handler to start the query.
	 */
	virtual void onQueryStateEnabledButNotStarted(IMeetingStartQueryHandler* pHandler) = 0;

    /**
	 * @brief Notification of the query is started.
	 * @param pHandler The handler to send query.
	 */
	virtual void onQueryStateStarted(IMeetingSendQueryHandler* pHandler) = 0;

    /**
	 * @brief Notification of the query setting is changed.
	 * @param eSetting The selected query setting.
	 */
	virtual void onQuerySettingChanged(MeetingAICompanionQuerySettingOptions eSetting) = 0;

    /**
	 * @brief Notification of the query failed to start.
	 * @param bTimeout true indicates is timeout. Otherwise means the user declines the request.
	 */
	virtual void onFailedToStartQuery(bool bTimeout) = 0;

    /**
	 * @brief Notification of receiving request to enable query.
	 * @param pHandler The handler to handle the request.
	 */
	virtual void onReceiveRequestToEnableQuery(IMeetingApproveEnableQueryHandler* pHandler) = 0;

    /**
	 * @brief Notification of receiving request to start query.
	 * @param pHandler The handler to handle the request.
	 */
	virtual void onReceiveRequestToStartQuery(IMeetingApproveStartQueryHandler* pHandler) = 0;

    /**
	 * @brief Notification of receiving query answer.
	 * @param pHandler The object of IMeetingAICompanionQueryItem.
	 */
	virtual void onReceiveQueryAnswer(IMeetingAICompanionQueryItem* pQueryItem) = 0;

    /**
	 * @brief Notification of receiving query enable action callback.
	 * @param pHandler The handler to enable the query.
	 */
	virtual void onQueryEnableActionCallback(IMeetingEnableQueryActionHandler* pHandler) = 0;

    /**
    * @brief Notification of getting or losing send query question privilege.
    * @param canSendQuery true indicates can send. Otherwise not.
	 */
	virtual void onSendQueryPrivilegeChanged(bool canSendQuery) = 0;

    /**
	 * @brief Notification of the request to send query failed.
	 * @param bTimeout true indicates that the request timed out. Otherwise means that the user declines the request.
	 */
	virtual void onFailedToRequestSendQuery(bool bTimeout) = 0;

    /**
	 * @brief Notification of receiving request to send query.
	 * @param handler The handler to handle the request.
	 */
	virtual void onReceiveRequestToSendQuery(IMeetingApproveSendQueryHandler* pHandler) = 0;
};


/**
 * @class IMeetingAICompanionQueryHelper
 * @brief Meeting AI companion query helper interface.
 */
class IMeetingAICompanionQueryHelper
{
public:
	virtual ~IMeetingAICompanionQueryHelper() {}
    /**
	 * @brief Sets the AI companion query callback event handler.
	 * @param event A pointer to the IMeetingAICompanionQueryHelperEvent that receives the AI companion query event. 
	 */
	virtual void SetEvent(IMeetingAICompanionQueryHelperEvent* event) = 0;

    /**
	 * @brief Determines whether the current user can change query setting.
	 * @param bCan true indicates that it can change the setting. Otherwise it can't change the setting.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError CanChangeQuerySetting(bool& bCan) = 0;

    /**
	 * @brief Change query setting.
	 * @param setting The query setting. For more details, see \link MeetingAICompanionQuerySettingOptions \endlink.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError ChangeQuerySettings(MeetingAICompanionQuerySettingOptions setting) = 0;

    /**
	 * @brief Gets the selected query setting.
	 * @return If the function succeeds, it will return the selected query setting. Otherwise not.
	 */
	virtual MeetingAICompanionQuerySettingOptions GetSelectedQuerySetting() = 0;

    /**
	 * @brief Determines whether the legal notice for the AI Companion query is available.
	 * @param bAvailable true indicates the legal notice for the AI Companion query is available. Otherwise the legal notice is not available.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError IsAICompanionQueryLegalNoticeAvailable(bool& bAvailable) = 0;

    /**
	 * @brief Gets the AI Companion query legal notices prompt.
	 * @return If the function succeeds, it will return the AI Companion query legal notices prompt. Otherwise, this function fails and returns nullptr.
	 */
	virtual const zchar_t* GetAICompanionQueryLegalNoticesPrompt() = 0;

    /**
	 * @brief Gets the AI Companion query legal notices explained.
	 * @return If the function succeeds, it will return the AI Companion query legal notices explained. Otherwise, this function fails and returns nullptr.
	 */
	virtual const zchar_t* GetAICompanionQueryLegalNoticesExplained() = 0;

    /**
	 * @brief Determines whether the legal notice for the AI Companion query privacy is available.
	 * @param bAvailable true indicates the legal notice for the AI Companion query privacy is available. Otherwise the legal notice is not available.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError IsAICompanionQueryPrivacyLegalNoticeAvailable(bool& bAvailable) = 0;

    /**
	 * @brief Gets the AI Companion query privacy legal notices prompt.
	 * @return If the function succeeds, it will return the AI Companion query privacy legal notices prompt. Otherwise, this function fails and returns nullptr.
	 */
	virtual const zchar_t* GetAICompanionQueryPrivacyLegalNoticesPrompt() = 0;
};

/**
 * @class IAICompanionFeatureTurnOnAgainHandler
 * @brief The handler to turn on AI companion feature again.
 */
class IAICompanionFeatureTurnOnAgainHandler
{
public:
	virtual ~IAICompanionFeatureTurnOnAgainHandler() {};
    /**
	 * @brief Gets the list of features that the participant turns off.
	 */
	virtual IList<AICompanionFeature>* GetFeatureList() = 0;
    /**
	 * @brief Gets the list of features that the assets are deleted when the feature is turned off by participant.
	 */
	virtual IList<AICompanionFeature>* GetAssetsDeletedFeatureList() = 0;
    /**
	 * @brief Turn on the auto AI Companion feature which was stopped by the participant before the host or cohost joined meeting.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError TurnOnAgain() = 0;
    /**
	 * @brief Agree the auto AI Companion feature turn off status. Keep the AI Companion feature off.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError AgreeTurnOff() = 0;
};

/**
 * @class IAICompanionFeatureSwitchHandler
 * @brief The handler to handle a user request to turn the AI Companion features on or off.
 */
class IAICompanionFeatureSwitchHandler
{
public:
	virtual ~IAICompanionFeatureSwitchHandler() {};
    /**
	 * @brief Gets the user ID who requests host to turn the AI Companion features on or off.
	 * @return the request user ID.
	 */
	virtual unsigned int GetRequestUserID() = 0;
    /**
	 * @brief Turn the AI Companion features on or off.
	 * @return true if turning on the AI Companion features. Otherwise, false.
	 */
	virtual bool IsTurnOn() = 0;
    /**
	 * @brief Agree the request to turn the AI companion features on or off.
	 * @param bDeleteAssets Specify whether delete the meeting assets when turning off the AI Companion features. 
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError Agree(bool bDeleteAssets) = 0;
    /**
	 * @brief Decline the request to turn the AI companion features on or off.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError Decline() = 0;
};


/**
 * @class IMeetingAICompanionCtrlEvent
 * @brief The event of AI companion controller.
 */
class IMeetingAICompanionCtrlEvent
{
public:
	virtual ~IMeetingAICompanionCtrlEvent() {}
    /**
	 * @brief Notification of the auto start AI Companion feature is turned off by a participant before the host join. Only the host or cohost can receive this callback.
	 * @param handler A pointer to the IAutoAICompanionFeatureTurnOnAgainHandler.
	 */
	virtual void onAICompanionFeatureTurnOffByParticipant(IAICompanionFeatureTurnOnAgainHandler* handler) = 0;
    /**
	 * @brief Notification of host receives the request to turn the AI Companion features on or off.
	 * @param handler A pointer to the IAICompanionFeatureSwitchHandler.
	 */
	virtual void onAICompanionFeatureSwitchRequested(IAICompanionFeatureSwitchHandler* handler) = 0;
    /**
	 * @brief Notification of the host handles the request to turn the AI Companion features on or off.
	 * @param timeout Specify the host not handle the request until timeout.
	 * @param bAgree Specify the host agrees to the request to turn the AI companion features on or off.
	 * @param bTurnOn Specify the host respond the request of turn on or off.
	 */
	virtual void onAICompanionFeatureSwitchRequestResponse(bool bTimeout, bool bAgree, bool bTurnOn) = 0;
    /**
	 * @brief Notification of the started AI Companion feature can't be turned off.
	 * @param features Specify the AI Companion features that can't be turned off.
	 */
	virtual void onAICompanionFeatureCanNotBeTurnedOff(IList<AICompanionFeature>* features) = 0;
	/**
     * @brief Sink the event when the host's client does not support handling the stop Notes request.
     * @note This callback is triggered when a participant requests to stop Notes, but the meeting host's client version does not support processing this request.
     */
	virtual void onHostUnsupportedStopNotesRequest() = 0;
};

/**
 * @class IMeetingAICompanionController
 * @brief Meeting AI Companion controller interface.
 */
class IMeetingAICompanionController
{
public:
    /**
	 * @brief Configure the meeting AI companion controller callback event handler.
	 * @param pEvent An object pointer to the IMeetingAICompanionCtrlEvent that receives the meeting AI companion callback event.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 * @note The SDK use pEvent to transmit the callback event to the user's application. If the function is not called or fails, the user's application is unable to retrieve the callback event.
	 */
	virtual SDKError SetEvent(IMeetingAICompanionCtrlEvent* pEvent) = 0;

    /**
	 * @brief Gets the smart summary helper.
	 * @return If the function succeeds, the return value is a pointer to IMeetingSmartSummaryHelper. Otherwise the function returns nullptr.
	 * @deprecated This interface is marked as deprecated, Use \link GetMeetingAICompanionSmartSummaryHelper() \endlink instead.
	 */
	virtual IMeetingSmartSummaryHelper* GetMeetingSmartSummaryHelper() = 0;

    /**
	 * @brief Gets the AI companion smart summary helper.
	 * @return If the function succeeds, the return value is a pointer to IMeetingAICompanionSmartSummaryHelper. Otherwise the function returns nullptr.
	 */
	virtual IMeetingAICompanionSmartSummaryHelper* GetMeetingAICompanionSmartSummaryHelper() = 0;

    /**
	 * @brief Gets the AI companion query helper.
	 * @return If the function succeeds, the return value is a pointer to IMeetingAICompanionQueryHelper. Otherwise the function returns nullptr.
	 */
	virtual IMeetingAICompanionQueryHelper* GetMeetingAICompanionQueryHelper() = 0;

    /**
	 * @brief Determines whether the current meeting supports turning off the AI Companion features.
	 * @return true if the current meeting supports turning off the AI Companion features. Otherwise, false.
	 */
	virtual bool IsTurnoffAllAICompanionsSupported() = 0;

    /**
	 * @brief Determines whether the current meeting supports turning on the AI Companion features.
	 * @return true if the current meeting supports turning on the AI Companion features. Otherwise, false.
	 */
	virtual bool IsTurnOnAllAICompanionsSupported() = 0;

    /**
	 * @brief Determines whether the current user can turn off the AI Companion features. 
	 * @return true if the user can turn off the AI Companion features. Otherwise, false.
	 */
	virtual bool CanTurnOffAllAICompanions() = 0;

    /**
	 * @brief Determines whether the current user can turn on the AI Companion features. 
	 * @return true if the user can turn on the AI Companion features. Otherwise, false.
	 */
	virtual bool CanTurnOnAllAICompanions() = 0;

    /**
	 * @brief Turn off the AI Companion features.
	 * @note All AI features including smart summary, smart recording and query can be turned off at once.
	 * @param deleteAssets Specify whether delete the meeting assets when turn off the AI Companion features. 
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError TurnOffAllAICompanions(bool bDeleteAssets) = 0;

    /**
	 * @brief Turn on the AI Companion features.
	 * @note smart summary and query can be turned on at once.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError TurnOnAllAICompanions() = 0;

    /**
	 * @brief Determines whether the current user can request host to turn off all started AI Companion features.
	 * @note if the current user join the meeting before the host, they can check CanTurnOffAllAICompanions to turn off the AI Companion features by himself/herself.
	 * All AI features include smart summary, smart recording and query can be requested to turn off at once.
	 * @return true if the user can request host to turn off all started AI Companion features. Otherwise, false.
	 */
	virtual bool CanRequestTurnoffAllAICompanions() = 0;

    /**
	 * @brief Determines whether the current user can request host to turn on all AI Companion features if they are enabled for the current meeting.
	 * @note smart summary and query can be requested to turn on at once.
	 * @return true if the user can request host to turn on the AI Companion features. Otherwise, false.
	 */
	virtual bool CanRequestTurnOnAllAICompanions() = 0;

    /**
	 * @brief request host to turn off all started AI Companion features.
	 * @note All AI features include smart summary, smart recording and query can be requested to turn off at once.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError RequestTurnoffAllAICompanions() = 0;

    /**
	 * @brief request host to turn on all AI Companion features if they are enabled for the current meeting.
	 * @note Only smart summary and query can be requested to turn on at once.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError RequestTurnOnAllAICompanions() = 0;
};

END_ZOOM_SDK_NAMESPACE
#endif