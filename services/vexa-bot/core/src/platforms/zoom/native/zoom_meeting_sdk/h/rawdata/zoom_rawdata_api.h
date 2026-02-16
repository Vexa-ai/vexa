/**
 * @file zoom_rawdata_api.h
 * @brief zoom raw data api.
 */
#ifndef _ZOOM_RAWDATA_API_H_
#define _ZOOM_RAWDATA_API_H_
#include "zoom_sdk_def.h"

BEGIN_ZOOM_SDK_NAMESPACE
class IZoomSDKVideoSourceHelper;
class IZoomSDKRenderer;
class IZoomSDKAudioRawDataHelper;
class IZoomSDKRendererDelegate;
class IZoomSDKShareSourceHelper;
extern "C"
{
	SDK_API bool HasRawdataLicense();
	SDK_API IZoomSDKVideoSourceHelper*  GetRawdataVideoSourceHelper();
	SDK_API IZoomSDKShareSourceHelper*  GetRawdataShareSourceHelper();
	SDK_API IZoomSDKAudioRawDataHelper* GetAudioRawdataHelper();
	SDK_API SDKError createRenderer(IZoomSDKRenderer** ppRenderer, IZoomSDKRendererDelegate* pDelegate);
	SDK_API SDKError destroyRenderer(IZoomSDKRenderer* pRenderer);
}

END_ZOOM_SDK_NAMESPACE
#endif