/**
 * @file meeting_video_interface.h
 * @brief Meeting Service Video Interface
 * 
 */
#ifndef _MEETING_VIDEO_INTERFACE_H_
#define _MEETING_VIDEO_INTERFACE_H_
#include "zoom_sdk_def.h"
#if defined(WIN32)
#include "zoom_sdk_util_define.h"
#endif
BEGIN_ZOOM_SDK_NAMESPACE

/** 
 * @brief Enumeration of video status of the user.
 * Here are more detailed structural descriptions.
 */
enum VideoStatus
{
	/** Video is on. */
	Video_ON, 
	/** Video is off. */
	Video_OFF, 
	/** Video is muted by host. */
	Video_Mute_ByHost, 
};
/**
 * @brief Enumeration of video quality of the user.
 * Here are more detailed structural descriptions.
 */
enum VideoConnectionQuality
{
	/** Unknown video quality status. */
	VideoConnectionQuality_Unknown = 0, 
	/** The video quality is poor. */
	VideoConnectionQuality_Bad,  
	/** The video quality is normal. */
	VideoConnectionQuality_Normal, 
	/** The video quality is good. */
	VideoConnectionQuality_Good,
};

typedef struct tagVideoSize
{
	int width;
	int height;
	tagVideoSize()
	{
		memset(this, 0, sizeof(tagVideoSize));   /** checked safe */
	}
}VideoSize;


/**
 * @brief Select and use any of the defined preference mode below when initializing the SDKVideoPreferenceSetting.
 * Video preference modes determined the video frame rate and resolution based on the user's bandwidth.
 * Here are more detailed structural descriptions.
 */
typedef enum
{
	/** Balance mode. Default Preference, no additional parameters needed. Zoom will do what is best under the current bandwidth situation and make adjustments as needed. */
	SDKVideoPreferenceMode_Balance, 
	/** Sharpness mode. Prioritizes a smooth video frame transition by preserving the frame rate as much as possible. */
	SDKVideoPreferenceMode_Sharpness, 
	/** Smoothness mode. Prioritizes a sharp video image by preserving the resolution as much as possible. */
	SDKVideoPreferenceMode_Smoothness, 
	/** Custom mode. Allows customization by providing the minimum and maximum frame rate. Use this mode if you have an understanding of your network behavior 
	 * and a clear idea on how to adjust the frame rate to achieve the desired video quality.
	 */
	SDKVideoPreferenceMode_Custom	
}SDKVideoPreferenceMode;


/**
 * @brief When setting custom modes, the developer provides the maximum and minimum frame rates.
 * If the current bandwidth cannot maintain the minimum frame rate, the video system will drop to the next lower resolution.
 * The default maximum and minimum frame rates for other modes are 0.
 */
typedef struct tagSDKVideoPreferenceSetting
{
	/** 0: Balance mode; 1: Smoothness mode; 2: Sharpness mode; 3: Custom mode */
	SDKVideoPreferenceMode mode; 
	/** 0 for the default value,minimum_frame_rate should be less than maximum_frame_rate, range: from 0 to 30 .out of range for frame-rate will use default frame-rate of Zoom	 */
	unsigned int minimumFrameRate; 	
	/** 0 for the default value,maximum_frame_rate should be less and equal than 30, range: from 0 to 30.out of range for frame-rate will use default frame-rate of Zoom */
	unsigned int maximumFrameRate; 
	tagSDKVideoPreferenceSetting()
	{
		mode = SDKVideoPreferenceMode_Balance;
		minimumFrameRate = 0;
		maximumFrameRate = 0;
	}
} SDKVideoPreferenceSetting;

/**
 * @class ISetVideoOrderHelper
 * @brief set video order helper interface.
 */
class ISetVideoOrderHelper
{
public:
	/**
	 * @brief Prepares to make a new video order.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 * @note If the function succeeds, the prepared video order you added by calling \link AddVideoToOrder \endlink will be cleared.
	 */
	virtual SDKError SetVideoOrderTransactionBegin() = 0;
	
	/**
	 * @brief Adds the assigned user into the prepared video order.
	 * @param userId The user you want to add into the prepared video order.
	 * @param position The position you want to place in the prepared video order.
	 * @return true if the user is added to the prepared video order successfully. Otherwise, false.
	 * @note The max number of the prepared video order is 49. If you assign many userId with the same order, only the last one will be applied.
	 * @note SetVideoOrderTransactionBegin() must be called before this function is called. Otherwise, false will be returned.
	 */
	virtual bool AddVideoToOrder(unsigned int userId, unsigned int position) = 0;
	
	/**
	 * @brief Makes a new video order.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 * @note SetVideoOrderTransactionBegin() must be called before this function is called. Otherwise, SDKERR_WRONG_USAGE will be returned.
	 */
	virtual SDKError SetVideoOrderTransactionCommit() = 0;
};

/**
 * @class IRequestStartVideoHandler
 * @brief Process after the user receives the requirement from the host to turn on the video.
 */
class IRequestStartVideoHandler
{
public:
	virtual ~IRequestStartVideoHandler(){};
	
	/**
	 * @brief Gets the user ID who asks to turn on the video.
	 * @return If the function succeeds, it returns the user ID. Otherwise, this function returns ZERO(0).
	 */
	virtual unsigned int GetReqFromUserId() = 0;
	
	/**
	 * @brief Ignores the requirement, returns nothing and finally self-destroys.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError Ignore() = 0;
	
	/**
	 * @brief Accepts the requirement, turns on the video and finally self-destroys.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError Accept() = 0;
	
	/**
	 * @brief Ignores the request to enable the video in the meeting and finally the instance self-destroys.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError Cancel() = 0;
};

/**
 * @brief Enumeration of camera control request type.
 * Here are more detailed structural descriptions.
 */
enum CameraControlRequestType
{
	CameraControlRequestType_Unknown = 0,
	CameraControlRequestType_RequestControl,
	CameraControlRequestType_GiveUpControl,
};

/**
 * @brief Enumeration of camera control request result.
 * Here are more detailed structural descriptions.
 */
enum CameraControlRequestResult
{
	CameraControlRequestResult_Approve,
	CameraControlRequestResult_Decline,
	CameraControlRequestResult_Revoke,
};

/**
 * @class ICameraControlRequestHandler
 * @brief Camera control request.
 */
class ICameraControlRequestHandler
{
public:
	virtual ~ICameraControlRequestHandler() {};
	/**
	 * @brief Accepts the requirement.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError Approve() = 0;
	
	/**
	 * @brief Declines the requirement and finally self-destroys.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError Decline() = 0;
};

/**
 * @class IMeetingVideoCtrlEvent
 * @brief Meeting video controller event callback
 */
class IMeetingVideoCtrlEvent
{
public:
	virtual ~IMeetingVideoCtrlEvent() {}
	
	/**
	 * @brief Callback event of the user video status changes.
	 * @param userId The user ID whose video status changes
	 * @param status New video status.
	 * @note Valid for both normal user and webinar attendee.
	 */
	virtual void onUserVideoStatusChange(unsigned int userId, VideoStatus status) = 0;
	
	/**
	 * @brief Callback event for when the video spotlight user list changes. Spotlight user means that the view will show only the specified user and won't change the view even other users speak.
	 * @param lstSpotlightedUserID spot light user list.
	 */
	virtual void onSpotlightedUserListChangeNotification(IList<unsigned int >* lstSpotlightedUserID) = 0;
	
	/**
	 * @brief Callback event of the requirement to turn on the video from the host.
	 * @param handler_ A pointer to the IRequestStartVideoHandler.
	 */
	virtual void onHostRequestStartVideo(IRequestStartVideoHandler* handler_) = 0;
	
	/**
	 * @brief Callback event of the active speaker video user changes. 
	 * @param userid The ID of user who becomes the new active speaker.
	 */
	virtual void onActiveSpeakerVideoUserChanged(unsigned int userid) = 0;
	
	/**
	 * @brief Callback event of the active video user changes. 
	 * @param userid The ID of user who becomes the new active speaker.
	 */
	virtual void onActiveVideoUserChanged(unsigned int userid) = 0;
	
	/**
	 * @brief Callback event of the video order changes.
     * @param orderList The video order list contains the user ID of listed users.
	 */
 	virtual void onHostVideoOrderUpdated(IList<unsigned int >* orderList) = 0;
	
	/**
	 * @brief Callback event of the local video order changes.
	 * @param localOrderList The lcoal video order list contains the user ID of listed users.
	 */
	virtual void onLocalVideoOrderUpdated(IList<unsigned int >* localOrderList) = 0;
	
	/**
	 * @brief Notification the status of following host's video order changed.
	 * @param follow Yes means the option of following host's video order is on, otherwise not.
	 */
	virtual void onFollowHostVideoOrderChanged(bool bFollow) = 0;
	
	/**
	 * @brief Callback event of the user video quality changes.
	 * @param userId The user ID whose video quality changes
	 * @param quality New video quality.
	 */
	virtual void onUserVideoQualityChanged(VideoConnectionQuality quality, unsigned int userid) = 0;
	
	/**
	 * @brief Callback event of video alpha channel mode changes.
	 * @param isAlphaModeOn true indicates it's in alpha channel mode. Otherwise, it's not.
	 */
	virtual void onVideoAlphaChannelStatusChanged(bool isAlphaModeOn) = 0;
	
	/**
	 * @brief Callback for when the current user receives a camera control request. This callback will be triggered when another user requests control of the current user's camera.
	 * @param userId The user ID that sent the request
	 * @param requestType The request type.
	 * @param pHandler A pointer to the ICameraControlRequestHandler.
	 */
	virtual void onCameraControlRequestReceived(unsigned int userId, CameraControlRequestType requestType, ICameraControlRequestHandler* pHandler) = 0;
	
	/**
	 * @brief Callback for when the current user is granted camera control access.
	 * @param userId The user ID that accepted the request
	 * @param isApproved The result of the camera control request.
	 */
	virtual void onCameraControlRequestResult(unsigned int userId, CameraControlRequestResult result) = 0;
};

/**
 * @brief Enumeration of possible results for pinning a user.
 */
enum PinResult
{
	/** Pinning succeeded. */
	PinResult_Success = 0,
	/** User counts less than 2. */
	PinResult_Fail_NotEnoughUsers,  
	/** Exceeded the maximum of 9 pinned users. */
	PinResult_Fail_ToMuchPinnedUsers, 
	/** User cannot be pinned (e.g., view-only mode, silent mode, or active speaker). */
	PinResult_Fail_UserCannotBePinned, 
	/** Other reasons.	 */
	PinResult_Fail_VideoModeDoNotSupport, 
	/** Current user has no privilege to pin. */
	PinResult_Fail_NoPrivilegeToPin, 
	/** Webinar and in view only meeting. */
	PinResult_Fail_MeetingDoNotSupport, 
	/** Too many users in the meeting to allow pinning. */
	PinResult_Fail_TooManyUsers,
	/** Unknown error. */
	PinResult_Unknown = 100,
};

enum SpotlightResult
{
	SpotResult_Success = 0,
	/** user counts less than 2 */
	SpotResult_Fail_NotEnoughUsers,  
	/** spotlighted user counts is more than 9 */
	SpotResult_Fail_ToMuchSpotlightedUsers,
	/** user in view only mode or silent mode or active */
	SpotResult_Fail_UserCannotBeSpotlighted, 
	/** user doesn't turn on video */
	SpotResult_Fail_UserWithoutVideo, 
	/** current user has no privilege to spotlight */
	SpotResult_Fail_NoPrivilegeToSpotlight,  
	/** user is not spotlighted */
	SpotResult_Fail_UserNotSpotlighted, 
	SpotResult_Unknown = 100,
};

/**
 * @class IMeetingCameraHelper
 * @brief Meeting camera helper interface
 */
class IMeetingCameraHelper
{
public:
	virtual ~IMeetingCameraHelper() {}
	
	/**
	 * @brief Gets the current controlled user ID.
	 * @return If the function succeeds, the return value is the user ID. Otherwise, this returns 0.
	 */
	virtual unsigned int GetUserId() = 0;
	
	/**
	 * @brief Whether the camera can be controlled or not.
	 * @return true if the user can control camera, false if they can't.
	 */
	virtual bool CanControlCamera() = 0;
	
	/**
	 * @brief Requests to control remote camera.	
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError RequestControlRemoteCamera() = 0;
	
	/**
	 * @brief Gives up control of the remote camera.	
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError GiveUpControlRemoteCamera() = 0;
	
	/**
	 * @brief Turns the camera to the left.
	 * @param range Rotation range,  10 <= range <= 100.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError TurnLeft(unsigned int range = 50) = 0;
	
	/**
	 * @brief Turns the camera to the right.
	 * @param range Rotation range,  10 <= range <= 100.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError TurnRight(unsigned int range = 50) = 0;
	
	/**
	 * @brief Turns the camera up.
	 * @param range Rotation range,  10 <= range <= 100.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError TurnUp(unsigned int range = 50) = 0;
	
	/**
	 * @brief Turns the camera down.
	 * @param range Rotation range,  10 <= range <= 100.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError TurnDown(unsigned int range = 50) = 0;
	
	/**
	 * @brief Zoom the camera in.
	 * @param range Rotation range,  10 <= range <= 100.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError ZoomIn(unsigned int range = 50) = 0;
	
	/**
	 * @brief Zoom the camera out.
	 * @param range Rotation range,  10 <= range <= 100.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError ZoomOut(unsigned int range = 50) = 0;
};

/**
 * @class IMeetingVideoController
 * @brief Meeting video controller interface
 */
class IMeetingVideoController
{
public:
	/**
	 * @brief Sets the meeting video controller callback event handler
	 * @param pEvent A pointer to the IRequestStartVideoHandler that receives the video controller event. 
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError SetEvent(IMeetingVideoCtrlEvent* pEvent) = 0;
	
	/**
	 * @brief Turn off the user's own video.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 * @note Valid for both Zoom style and customize user interface mode.
	 */
	virtual SDKError MuteVideo() = 0;
	
	/**
	 * @brief Turn on the user's own video.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 * @note Valid for both Zoom style and customize user interface mode.
	 */
	virtual SDKError UnmuteVideo() = 0;
	
	/**
	 * @brief Determines if it is able to spotlight the video of the specified user in the meeting. 
	 * @param userid Specifies the user ID to be determined.
	 * @param [out] result Indicates if it is able to spotlight. It validates only when the return value is SDKERR_SUCCESS.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 * @note Valid for both Zoom style and customize user interface mode.
	 */
	virtual SDKError CanSpotlight(unsigned int userid, SpotlightResult& result) = 0;
	
	/**
	 * @brief Determines if it is able to unspotlight the video of the specified user in the meeting. 
	 * @param userid Specifies the user ID to be determined.
	 * @param [out] result Indicates if it is able to unspotlight. It validates only when the return value is SDKERR_SUCCESS.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 * @note Valid for both Zoom style and customize user interface mode.
	 */
	virtual SDKError CanUnSpotlight(unsigned int userid, SpotlightResult& result) = 0;
	
	/**
	 * @brief Spotlight the video of the assigned user to the first view.
	 * @param userid Specifies the user ID to be spotlighted.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 * @note Valid for both Zoom style and customize user interface mode.
	 */
	virtual SDKError SpotlightVideo(unsigned int userid) = 0;
	
	/**
	 * @brief Unspotlight the video of the assigned user to the first view.
	 * @param userid Specifies the user ID to be unspotlighted.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 * @note Valid for both Zoom style and customize user interface mode.
	 */
	virtual SDKError UnSpotlightVideo(unsigned int userid) = 0;
	
	/**
	 * @brief Unpin all the videos from the first view.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 * @note Valid for both Zoom style and customize user interface mode.
	 */
	virtual SDKError UnSpotlightAllVideos() = 0;
	
	/**
	 * @brief Gets the list of all the spotlighted user in the meeting.
	 * @return If the function succeeds, it returns the list of the spotlighted user in the meeting. Otherwise, this function fails and returns nullptr.
	 * @note Valid for both Zoom style and customize user interface mode.
	 */
	virtual IList<unsigned int >* GetSpotlightedUserList() = 0;
	
	/**
	 * @brief Query if it is able to demand the specified user to turn on the video.
	 * @param userid Specifies the user ID to query.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 * @note Valid for both Zoom style and customize user interface mode.
	 */
	virtual SDKError CanAskAttendeeToStartVideo(unsigned int userid) = 0;
	
	/**
	 * @brief Demand the assigned user to turn on the video.
	 * @param userid Specifies the user ID to demand.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 * @note Valid for both Zoom style and customize user interface mode.
	 */
	virtual SDKError AskAttendeeToStartVideo(unsigned int userid) = 0;
	
	/**
	 * @brief Query if it is able to demand the specified user to turn off the video.
	 * @param userid Specifies the user ID to query.  
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 * @note Valid for both Zoom style and customize user interface mode.
	 */
	virtual SDKError CanStopAttendeeVideo(unsigned int userid) = 0;
	
	/**
	 * @brief Turn off the video of the assigned user.
	 * @param userid Specifies the user ID to turn off.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 * @note Valid for both Zoom style and customize user interface mode.
	 */
	virtual SDKError StopAttendeeVideo(unsigned int userid) = 0;
	
	/**
	 * @brief Determines if the following host video order feature is supported.
	 * @return true indicates to support the following host video order feature.
	 */
	virtual bool IsSupportFollowHostVideoOrder() = 0;
	
	/**
	 * @brief Enables or disable follow host video order mode.
	 * @param bEnable true indicates to set to enable the follow host video order mode.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError EnableFollowHostVideoOrder(bool bEnable) = 0;
	
	/**
	 * @brief Determines if the follow host video mode is enabled.
	 * @return true indicates to enable the mode. 
	 */
	virtual bool IsFollowHostVideoOrderOn() = 0;
	
	/**
	 * @brief Gets the video order list.
	 * @return If the function succeeds, the return value the is video order list. Otherwise, this function fails and returns nullptr.
	 */
	virtual IList<unsigned int >* GetVideoOrderList() = 0;
	

	/**
	 * @brief Determines if the incoming video is stopped.
	 * @return true indicates that the incoming video is stopped. 
	 */
	virtual bool IsIncomingVideoStopped() = 0;
	
#if defined(WIN32)
	/**
	 * @brief Determines if it is able to pin the video of the specified user to the first view. 
	 * @param userid Specifies the user ID to be determined.
	 * @param [out] result Indicates if it is able to pin. It validates only when the return value is SDKERR_SUCCESS.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 * @note Valid only for Zoom style user interface mode. 
	 */
	virtual SDKError CanPinToFirstView(unsigned int userid, PinResult& result) = 0;
	
	/**
	 * @brief Pin the video of the assigned user to the first view.
	 * @param userid Specifies the user ID to be pinned. 
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 * @note Valid only for Zoom style user interface mode. 
	 */
	virtual SDKError PinVideoToFirstView(unsigned int userid) = 0;
	
	/**
	 * @brief Unpin the video of the assigned user from the first view.
	 * @param userid Specifies the user ID to be unpinned. 
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 * @note Valid only for Zoom style user interface mode. 
	 */
	virtual SDKError UnPinVideoFromFirstView(unsigned int userid) = 0;
	
	/**
	 * @brief Unpin all the videos from the first view.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 * @note Valid only for Zoom style user interface mode.
	 */
	virtual SDKError UnPinAllVideosFromFirstView() = 0;
	
	/**
	 * @brief Gets the list of all the pinned user in the first view.
	 * @return If the function succeeds, it returns the list of the pinned user in the first view. Otherwise, this function fails and returns nullptr.
	 * @note Valid only for Zoom style user interface mode.
	 */
	virtual IList<unsigned int >* GetPinnedUserListFromFirstView() = 0;
	
	/**
	 * @brief Determines if it is able to pin the video of the specified user to the second view. 
	 * @param userid Specifies the user ID to be determined.
	 * @param [out] result Indicates if it is able to pin. It validates only when the return value is SDKERR_SUCCESS.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 * @note Valid only for Zoom style user interface mode. 
	 */
	virtual SDKError CanPinToSecondView(unsigned int userid, PinResult& result) = 0;
	
	/**
	 * @brief Pin the video of the assigned user to the second view.
	 * @param userid Specifies the user ID to be pinned. 
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 * @note Valid only for Zoom style user interface mode. 
	 */
	virtual SDKError PinVideoToSecondView(unsigned int userid) = 0;
	
	/**
	 * @brief Unpin the video of the assigned user from the second view.
	 * @param userid Specifies the user ID to be unpinned. 
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 * @note Valid only for Zoom style user interface mode. 
	 */
	virtual SDKError UnPinVideoFromSecondView(unsigned int userid) = 0;
	
	/**
	 * @brief Gets the list of all the pinned user in the second view.
	 * @return If the function succeeds, it returns the list of the pinned user in the second view. Otherwise, this function fails and returns nullptr.
	 * @note Valid only for Zoom style user interface mode.
	 */
	virtual IList<unsigned int >* GetPinnedUserListFromSecondView() = 0;
	
	/**
	 * @brief Display or not the user who does not turn on the video in the video all mode.
	 * @param true indicates to hide, false show.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 * @note Valid only for Zoom style user interface mode.
	 */
	virtual SDKError HideOrShowNoVideoUserOnVideoWall(bool bHide) = 0;
	
	/**
	 * @brief Display or not the userself's view.
	 * @param true indicates to hide, false show.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 * @note Valid only for Zoom style user interface mode.
	 */
	virtual SDKError HideOrShowSelfView(bool bHide) = 0;
	
	/**
	 * @brief Gets set video order helper interface.
	 * @return If the function succeeds, the return value is a pointer to ISetVideoOrderHelper. Otherwise returns nullptr.
	 */
	virtual ISetVideoOrderHelper* GetSetVideoOrderHelper() = 0;
	
	/**
	 * @brief Gets camera controller interface.
	 * @return If the function succeeds, the return value is a pointer to ICameraController. Otherwise returns nullptr.
	 */
	virtual ICameraController* GetMyCameraController() = 0;
	
	/**
	 * @brief Stops the incoming video.
	 * @param bStop true indicates to stop the incoming video.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error and returns an error.
	 * @note Valid for both Zoom style and customize user interface mode.
	 */
	virtual SDKError StopIncomingVideo(bool bStop) = 0;
	
	/**
	 * @brief Determines if show the last used avatar in the meeting.
	 * @param bShow true indicates to show the last used avatar.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error and returns an error.
	 */
	virtual SDKError ShowAvatar(bool bShow) = 0;
	
	/**
	 * @brief Determines if the meeting is showing the avatar.
	 * @return true indicates the meeting is showing the avatar.
	 */
	virtual bool IsShowAvatar() = 0;
#endif

	/**
	 * @brief Gets camera helper interface.
	 * @return If the function succeeds, the return value is a pointer to IMeetingCameraHelper. Otherwise returns nullptr.
	 */
	virtual IMeetingCameraHelper* GetMeetingCameraHelper(unsigned int userid) = 0;
	
	/**
	 * @brief Revoke camera control privilege.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError RevokeCameraControlPrivilege() = 0;
	
	/**
	 * @brief Determines if alpha channel mode can be enabled. 
	 * @return true indicates it can be enabled. Otherwise false.
	 */
	virtual bool CanEnableAlphaChannelMode() = 0;
	
	/**
	 * @brief Enables or disable video alpha channel mode.
	 * @param enable true indicates to enable alpha channel mode. Otherwise, disable it.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError EnableAlphaChannelMode(bool enable) = 0;
	
	/**
	 * @brief Determines if alpha channel mode is enabled.
	 * @return true indicates alpha channel mode is enabled. Otherwise false.
	 */
	virtual bool IsAlphaChannelModeEnabled() = 0;
	
	/**
	 * @brief Gets the size of user's video.
	 * @param userid Specifies the user ID. The user id should be 0 when not in meeting.
	 * @return The size of user's video.
	 */
	virtual VideoSize GetUserVideoSize(unsigned int userid) = 0;
	
	/**
	 * @brief Sets the video quality preference that automatically adjust user's video to prioritize frame rate versus resolution based on the current bandwidth available.
	 * @param preferenceSetting Specifies the video quality preference.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError SetVideoQualityPreference(SDKVideoPreferenceSetting preferenceSetting) = 0;
	
	/**
	 * @brief Enables or disables contrast enhancement effect for speaker video.
	 * @param enable true to enable contrast enhancement effect, false to disable.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError EnableSpeakerContrastEnhance(bool enable) = 0;
	
	/**
	 * @brief Determines if contrast enhancement effect for speaker video is enabled.
	 * @return true indicates contrast enhancement effect is enabled. Otherwise false.
	 */
	virtual bool IsSpeakerContrastEnhanceEnabled() = 0;
};
END_ZOOM_SDK_NAMESPACE
#endif