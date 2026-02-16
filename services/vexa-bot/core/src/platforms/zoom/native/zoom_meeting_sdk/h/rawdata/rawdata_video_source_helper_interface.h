/**
 * @file rawdata_video_source_helper_interface.h
 * @brief Raw Data Video Source Helper Interface.
 */
 
#ifndef _RAWDATA_VIDEO_SOURCE_HELPER_INTERFACE_H_
#define _RAWDATA_VIDEO_SOURCE_HELPER_INTERFACE_H_
#include "zoom_sdk_def.h"

class YUVProcessDataI420;

BEGIN_ZOOM_SDK_NAMESPACE
class IZoomSDKPreProcessor 
{
public:
	virtual ~IZoomSDKPreProcessor(){}
	virtual void onPreProcessRawData(YUVProcessDataI420* rawData) = 0;
};

/////////////////////////////////////////////////////////////////////////////////////////////////////////
class IZoomSDKVideoSender 
{
public:
	virtual ~IZoomSDKVideoSender(){}
	virtual SDKError sendVideoFrame(char* frameBuffer, int width, int height, int frameLength, int rotation, FrameDataFormat format = FrameDataFormat_I420_FULL) = 0;
};

struct VideoSourceCapability
{
	unsigned int width;
	unsigned int height;
	unsigned int frame;

	VideoSourceCapability()
	{
		Reset();
	}

	VideoSourceCapability(unsigned int w, unsigned int h, unsigned int f)
	{
		width = w;
		height = h;
		frame = f;
	}

	VideoSourceCapability& operator=(const VideoSourceCapability& ins)
	{
		width = ins.width;
		height = ins.height;
		frame = ins.frame;
		return *this;
	}

	VideoSourceCapability(const VideoSourceCapability& ins)
	{
		*this = ins;  
	}

	void Reset()
	{
		width = 0;
		height = 0;
		frame = 0;
	}
};

class IZoomSDKVideoSource 
{
public:
	virtual ~IZoomSDKVideoSource(){}
	virtual	void onInitialize(IZoomSDKVideoSender* sender, IList<VideoSourceCapability >* support_cap_list, VideoSourceCapability& suggest_cap) = 0;
	virtual void onPropertyChange(IList<VideoSourceCapability >* support_cap_list, VideoSourceCapability suggest_cap) = 0;
	virtual void onStartSend() = 0;
	virtual void onStopSend() = 0;
	virtual void onUninitialized() = 0;
};

/////////////////////////////////////////////////////////////////////////////////////////////////////////
class IZoomSDKVideoSourceHelper 
{
public:
	virtual ~IZoomSDKVideoSourceHelper(){}
	/**
	 * @brief Sets a PreProcessor to edit the zoom camera video source before send to other.
	 */
    virtual SDKError setPreProcessor(IZoomSDKPreProcessor* processor) = 0;		
	
	/**
	 * @brief Sets external video source for capture video by self.
	 */
    virtual SDKError setExternalVideoSource(IZoomSDKVideoSource* source) = 0;  
};
END_ZOOM_SDK_NAMESPACE
#endif