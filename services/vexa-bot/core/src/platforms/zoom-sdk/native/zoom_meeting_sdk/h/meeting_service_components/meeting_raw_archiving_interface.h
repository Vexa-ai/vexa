/**
 * @file meeting_raw_archiving_interface.h
 * @brief Meeting Raw Archiving Interface. 
 */
#ifndef _MEETING_RAW_ARCHIVING_INTERFACE_H_
#define _MEETING_RAW_ARCHIVING_INTERFACE_H_
#include "zoom_sdk_def.h"

BEGIN_ZOOM_SDK_NAMESPACE
/**
 * @class IMeetingRawArchivingController
 * @brief Meeting raw archiving controller interface.
 */
class IMeetingRawArchivingController
{
public:
	/**
	 * @brief start raw archiving,call this method can get rawdata receive privilege.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError StartRawArchiving() = 0;

	/**
	 * @brief stop raw archiving, call this method reclaim rawdata receive privilege.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError StopRawArchiving() = 0;
};

END_ZOOM_SDK_NAMESPACE
#endif