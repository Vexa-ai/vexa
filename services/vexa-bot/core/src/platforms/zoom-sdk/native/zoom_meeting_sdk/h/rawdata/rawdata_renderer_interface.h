/**
 * @file rawdata_renderer_interface.h
 * @brief Raw Data Renderer Interface.
 */
#ifndef _RAWDATA_RENDERER_INTERFACE_H_
#define _RAWDATA_RENDERER_INTERFACE_H_
#include "zoom_sdk_def.h"

class YUVRawDataI420;

BEGIN_ZOOM_SDK_NAMESPACE
typedef enum ZoomSDKResolution
{
	ZoomSDKResolution_90P = 0,
	ZoomSDKResolution_180P,
	ZoomSDKResolution_360P,
	ZoomSDKResolution_720P,
	ZoomSDKResolution_1080P,
	ZoomSDKResolution_NoUse = 100
}ZoomSDKResolution;

typedef enum
{
	RAW_DATA_TYPE_VIDEO = 0,
	RAW_DATA_TYPE_SHARE,
}ZoomSDKRawDataType;

class IZoomSDKRendererDelegate
{
public:
	enum RawDataStatus
	{
		RawData_On,
		RawData_Off,
	};
	/**
	 * @brief Notify the current renderer object is going to be destroyed. 
	 * @note After you handle this callback, you should never user this renderer object any more.
	 */
	virtual void onRendererBeDestroyed() = 0;

	virtual void onRawDataFrameReceived(YUVRawDataI420* data) = 0;
	virtual void onRawDataStatusChanged(RawDataStatus status) = 0;
	virtual ~IZoomSDKRendererDelegate() {}
};

class IZoomSDKRenderer
{
public:
	virtual SDKError setRawDataResolution(ZoomSDKResolution resolution) = 0;
	/**
	 * @brief Subscribe to the video or share's raw data.
	 * @param subscribeId: If 'type' is RAW_DATA_TYPE_VIDEO, 'subscribeId' refers to the user ID, otherwise it refers to the shared source ID of user.
	 * @param type: Specify the raw data type.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError subscribe(uint32_t subscribeId, ZoomSDKRawDataType type) = 0;
	virtual SDKError unSubscribe() = 0;
	virtual ZoomSDKResolution getResolution() = 0;
	virtual ZoomSDKRawDataType getRawDataType() = 0;
	/**
	 * @brief Gets the subscribed ID specified when subscribing.
	 * @return subscribed id.
	 */
	virtual uint32_t getSubscribeId() = 0;
	virtual ~IZoomSDKRenderer(){}
};
END_ZOOM_SDK_NAMESPACE
#endif