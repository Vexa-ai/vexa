/**
 * @file meeting_reminder_ctrl_interface.h
 * @brief Meeting Service Phone Interface.
 * Valid for both ZOOM style and user custom interface mode.
 */
#ifndef _MEETING_REMINDER_CTRL_INTERFACE_H_
#define _MEETING_REMINDER_CTRL_INTERFACE_H_
#include "zoom_sdk_def.h"

BEGIN_ZOOM_SDK_NAMESPACE

/**
 * @brief Enumeration of disclaimer dialog type.
 * Here are more detailed structural descriptions.
 */
enum MeetingReminderType
{
	/** Reminder type of login. */
	TYPE_LOGIN_REQUIRED,
	/** Reminder type of start or join meeting. */
	TYPE_START_OR_JOIN_MEETING,
	/** Reminder type of record reminder. */
	TYPE_RECORD_REMINDER,
	/** Reminder type of record disclaimer */
	TYPE_RECORD_DISCLAIMER,
	/** Reminder type of live stream disclaimer. */
	TYPE_LIVE_STREAM_DISCLAIMER,
	/** Reminder type of archive disclaimer. */
	TYPE_ARCHIVE_DISCLAIMER,
	/** Reminder type of join webinar as panelist. */
	TYPE_WEBINAR_AS_PANELIST_JOIN,
	/** Reminder type of Terms of service or privacy statement changed. */
	TYPE_TERMS_OF_SERVICE, 
	/** Reminder type of smart summary disclaimer. */
	TYPE_SMART_SUMMARY_DISCLAIMER, 
	/** Reminder type of smart summary enable request. This type is marked as deprecated. Replaced with callback \link IMeetingAICompanionSmartSummaryHelperEvent::onSmartSummaryEnableActionCallback \endlink */
	TYPE_SMART_SUMMARY_ENABLE_REQUEST_REMINDER, 
	/** Reminder type of query disclaimer. */
	TYPE_QUERY_DISCLAIMER,  
	/** Reminder type of query enable request. This type is marked as deprecated. */
	TYPE_QUERY_ENABLE_REQUEST_REMINDER, 
	/** Reminder type of enable smart summary. This type is marked as deprecated. Replaced with callback \link IMeetingAICompanionSmartSummaryHelperEvent::onSmartSummaryEnableActionCallback \endlink */
	TYPE_ENABLE_SMART_SUMMARY_REMINDER,
	/** Reminder type of webinar promote attendee. */
	TYPE_WEBINAR_ATTENDEE_PROMOTE_REMINDER,	
	/** Reminder type of joining a meeting with private mode. */
	TYPE_JOIN_PRIVATE_MODE_MEETING_REMINDER,
	/** Reminder type to enable smart recording request. This type is marked as deprecated. */
	TYPE_SMART_RECORDING_ENABLE_REQUEST_REMINDER,
	/** Reminder type to enable smart recording. This type is marked as deprecated. */
	TYPE_ENABLE_SMART_RECORDING_REMINDER,
	/** Reminder type of AICompanionPlus disclaimer. This type is marked as Deprecated. */
	TYPE_AI_COMPANION_PLUS_DISCLAIMER,
	/** Reminder type of Close Caption disclaimer. */
	TYPE_CLOSED_CAPTION_DISCLAIMER,
	/** Reminder type of disclaimers combination. */
	TYPE_MULTI_DISCLAIMER,
	/** Reminder type for a join meeting connector with guest mode. */
	TYPE_JOIN_MEETING_CONNECTOR_AS_GUEST_REMINDER,
	/** Reminder type of common disclaimer. */
	TYPE_COMMON_DISCLAIMER,
	/** Reminder type of custom AI companion disclaimer. */
	TYPE_CUSTOM_AI_COMPANION_DISCLAIMER,
	/** Reminder type of AI Companion restrict notify disclaimer. */
	TYPE_AIC_RESTRICT_NOTIFY_DISCLAIMER,
};

/**
 * @brief Enumeration of action type which user should take.
 * Here are more detailed structural descriptions.
 */
enum ActionType
{
	/** Need no more action. */
	ACTION_TYPE_NONE,
	/** Need to sign in. */
	ACTION_TYPE_NEED_SIGN_IN,
	/** Need to switch account. */
	ACTION_TYPE_NEED_SWITCH_ACCOUNT,
};

#if (defined WIN32 )
/**
 * @brief The position of the window. The coordinate of position is that of monitor when the parent window is null. If the the parent window is not null, the position coordinate is that of the parent window.
 * Here are more detailed structural descriptions.
 */
typedef struct tagMultiReminderUIConfig
{
	/** Specifies the X-axis coordinate of the top-left corner of the multi-reminder window in the parent window. */
	int left;
	/** Specifies the Y-axis coordinate of the top-left corner of the multi-reminder window in the parent window. */
	int top;
	/** Specifies the window handle of the parent window. If the value is nullptr, the position coordinate is the monitor coordinate. */
	HWND hParent;
	/** Specifies the background color of the multi-reminder window. */
	unsigned long background_color;
	tagMultiReminderUIConfig()
	{
		left = 0;
		top = 0;
		hParent = nullptr;
		background_color = 0xffffff;
	}
}MultiReminderUIConfig;
#endif

/**
 * @class IMeetingReminderContent
 * @brief the interface of reminder dialog content.
 */
class IMeetingReminderContent
{
public:
	virtual ~IMeetingReminderContent() {};
	
	/**
	 * @brief Gets the type of the reminder.
	 */
	virtual MeetingReminderType  GetType() = 0;
	
	/**
	 * @brief Gets the title of the reminder dialog content.
	 */
	virtual const zchar_t* GetTitle() = 0;
	
	/**
	 * @brief Gets the detail content of the reminder dialog content.
	 */
	virtual const zchar_t* GetContent() = 0;
	
	/**
	 * @brief Determines whether block the user join or stay in the meeting
	 * @return true indicates block the user join or stay in the meeting. Otherwise false.
	 */
	virtual bool  IsBlocking() = 0;
	
	/**
	 * @brief Gets the type of the action which user should take after receiving this reminder content.
	 */
	virtual ActionType GetActionType() = 0;
	
	/**
	 * @brief Gets a List of reminder's type.
	 * @return List of the reminder's type. 
	 */
	virtual IList<MeetingReminderType >* GetMultiReminderTypes() = 0;

};

/**
 * @class IMeetingReminderHandler
 * @brief the interface to handle the reminder dialog.
 */
class IMeetingReminderHandler
{
public:
	virtual ~IMeetingReminderHandler() {};
	
	/**
	 * @brief Ignore the reminder.
	 */
	virtual SDKError  Ignore() = 0;
	
	/**
	 * @brief Accept the reminder.
	 */
	virtual SDKError  Accept() = 0;
	
	/**
	 * @brief Decline the reminder.
	 */
	virtual SDKError  Decline() = 0;
	
	/**
	 * @brief Sets not show the disclaimer in subsequent meetings.
	 */
	virtual SDKError  SetHideFeatureDisclaimers() = 0;
	
	/**
	 * @brief Is need explicit consent for AI custom disclaimer. \link TYPE_CUSTOM_AI_COMPANION_DISCLAIMER \endlink .
	 * @return true indicates explicit consent is required. Before agreeing to AIC disclaimer, the user's video and audio will be blocked. 
	 *  Otherwise means explicit consent is not required and video and audio will not be blocked.
	 */
	virtual bool IsNeedExplicitConsent4AICustomDisclaimer() = 0;
};

/** 
 * @brief Enumeration of meeting feature enable option.
 */
enum FeatureEnableOption
{
	/** do not enable. */
	EnableOption_None,
	/** enable for this meeting. */
	EnableOption_Once,
	/** enable for this and all future meetings on this account. */
	EnableOption_Always,
};

/**
 * @class IMeetingEnableReminderHandler
 * @brief if the current user can control web setting, they will get this handler when an attendee requests to start the smart recording feature or start feature by itself.
 */
class IMeetingEnableReminderHandler
{
public:
	virtual ~IMeetingEnableReminderHandler() {};
	
	/**
	 * @brief Sets the option indicating which meetings feature will be enabled for.
	 * @param option Specify the option. 	
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError SetEnableOption(FeatureEnableOption option) = 0;
	
	/**
	 * @brief Starts the feature.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError Start() = 0;
	
	/**
	 * @brief Decline the reminder.
	 * @param bDeclineAll true indicates decline all reminders,and participants cannot send requests again until the host changes the setting. false means that the host declines only this specific request, not all requests.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError Decline(bool bDeclineAll) = 0;
	
	/**
	 * @brief Ignore the reminder.
	 */
	virtual SDKError Ignore() = 0;
};

/**
 * @class IMeetingReminderEvent
 * @brief Callback event to enable showing the reminder dialog.
 */
class IMeetingReminderEvent
{
public:
	virtual ~IMeetingReminderEvent() {}
	
	/**
	 * @brief Callback event of the reminder dialog show.
	 * @param content The detail content in the reminder dialog.
	 * @param handle A pointer to the IMeetingReminderHandler.
	 */
	virtual void onReminderNotify(IMeetingReminderContent* content, IMeetingReminderHandler* handle) = 0;
	
	/**
	 * @brief Callback event of the enable reminder dialog show.
	 * @param content The detail content in the reminder dialog.
	 * @param handle A pointer to the IMeetingReminderHandler.
	 */
	virtual void onEnableReminderNotify(IMeetingReminderContent* content, IMeetingEnableReminderHandler* handle) = 0;
};

/**
 * @class IMeetingReminderController
 * @brief Meeting reminder dialog interface.
 */
class IMeetingReminderController
{
public:
	/**
	 * @brief Sets meeting reminder controller callback event handler
	 * @param pEvent A pointer to the IMeetingReminderEvent that receives reminder callback event. 
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError SetEvent(IMeetingReminderEvent* pEvent) = 0;

#if (defined WIN32 )
	/**
	 * @brief Sets the customized config of multi-reminder disclaimer. 
	 * @param config the customized config of multi-reminder disclaimer.
	 * @note Valid for user custom interface mode only.	
	 */
	virtual void SetMultiReminderDisclaimerUIConfig(MultiReminderUIConfig config) = 0;
	
	/**
	 * @brief Update the position and size of multi-reminder disclaimer window when its parent window moves or changes size.
	 * @note This interface should be invoked when the OnSize and OnMove messages of the parent window recieved. Valid for user custom interface mode only.
	 */
	virtual void UpdateMultiReminderDisclaimerUI() = 0;
#endif
};
END_ZOOM_SDK_NAMESPACE
#endif