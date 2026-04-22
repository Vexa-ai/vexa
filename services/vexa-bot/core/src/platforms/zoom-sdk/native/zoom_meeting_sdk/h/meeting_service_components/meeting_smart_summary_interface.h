#ifndef _MEETING_SMART_SUMMARY_INTERFACE_H_
#define _MEETING_SMART_SUMMARY_INTERFACE_H_
#include "zoom_sdk_def.h"
#include "meeting_ai_companion_interface.h"

BEGIN_ZOOM_SDK_NAMESPACE
/**
 * @class IMeetingSmartSummaryControllerEvent
 * @brief Meeting smart summary callback event.
 * @deprecated This class is marked as deprecated, and is replaced by class IMeetingSmartSummaryHelperEvent.
 */

class IMeetingSmartSummaryControllerEvent
{
public:
	/**
	 * @brief Callback event when smart summary status changes.
	 * @param isStarted true indicates smart summary is started, false means not.
	 */
	virtual void onSmartSummaryStatusChange(bool isStarted) = 0;
	/**
	 * @brief Callback event when a user requests host to start smart summary.
	 * @param senderId The user who requests host to start smart summary.
	 * @param handler The handler to handle the smart summary start request.
	 */
	virtual void onSmartSummaryPrivilegeRequested(unsigned int senderId, ISmartSummaryPrivilegeHandler* handler) = 0;
	/**
	 * @brief Callback event when the host handle the smart summary request.
	 * @param timeout true indicates the host not handle the request until timeout.
	 * @param decline true indicates the host decline the request, false means the host agree the request.
	 */
	virtual void onSmartSummaryStartReqResponse(bool timeout, bool decline) = 0;
	/**
	 * @brief Callback event when the user need to go to web to enable the smart summary feature.
	 * @note Valid for both ZOOM style and user custom interface mode. The callback will only be triggered for Business account. 
	 * And the smart summary feature will be enabled for the future meeting, but not enable the current meeting if the user enables the setting in web.
	 */
	virtual void onEnableSmartSummary() = 0;

	virtual ~IMeetingSmartSummaryControllerEvent() {}

};
/**
 * @class IMeetingSmartSummaryController
 * @brief Meeting smart summary controller interface.
 * @deprecated This class is marked as deprecated, and is replaced by class IMeetingSmartSummaryHelper.
 */
class IMeetingSmartSummaryController : public IMeetingSmartSummaryHelper
{
public:
	virtual ~IMeetingSmartSummaryController() {}
	/**
	 * @brief Sets the smart summary callback event handler.
	 * @param event A pointer to the IMeetingSmartSummaryControllerEvent that receives the smart summary event. 
	 */
	virtual void SetEvent(IMeetingSmartSummaryControllerEvent* event) = 0;
};

END_ZOOM_SDK_NAMESPACE
#endif