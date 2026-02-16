/**
 * @file meeting_waiting_room_interface.h
 * @brief Meeting Service Waiting Room Interface.
 * Valid only for Zoom style user interface mode.
 */
#ifndef _MEETING_WaitingRoom_INTERFACE_H_
#define _MEETING_WaitingRoom_INTERFACE_H_
#include "zoom_sdk_def.h"

BEGIN_ZOOM_SDK_NAMESPACE

/**
 * @brief Enumeration of waiting room layout type.
 * Here are more detailed structural descriptions.
 */
enum WaitingRoomLayoutType
{
	WaitingRoomLayoutType_Default,
	WaitingRoomLayoutType_Logo,
	WaitingRoomLayoutType_Video
};

/**
 * @brief Enumeration of custom waiting room data download status.
 * Here are more detailed structural descriptions.
 */
enum CustomWaitingRoomDataStatus
{
	CustomWaitingRoomDataStatus_Init,
	CustomWaitingRoomDataStatus_Downloading,
	CustomWaitingRoomDataStatus_Download_OK,
	CustomWaitingRoomDataStatus_Download_Failed
};

/**
 * @brief The waiting room page color.
 */
struct WaitingRoomBrandingPageColor {
	ZoomSDKColor background_color;
};

/**
 * @brief The waiting room text color.
 */
struct WaitingRoomBrandingTextColor {
	ZoomSDKColor primary_color;
	ZoomSDKColor secondary_color;
	ZoomSDKColor paragraph_color;
	ZoomSDKColor hyperlink_color;
};

/** 
 * @brief The waiting room button color.
 */
struct WaitingRoomBrandingButtonColor {
	ZoomSDKColor primary_button_color;
};

/** 
 * @brief The WaitingRoom Customize Data Info.
 * Here are more detailed structural descriptions..
 */
typedef struct CustomWaitingRoomData_s
{
	CustomWaitingRoomData_s()
		:type(WaitingRoomLayoutType_Default)
		, status(CustomWaitingRoomDataStatus_Init)
	{
		title = nullptr;
		description = nullptr;
		logo_path = nullptr;
		video_path = nullptr;
		image_path = nullptr;
	}
	const zchar_t* title;
	const zchar_t* description;
	const zchar_t* logo_path;
	const zchar_t* video_path;
	const zchar_t* image_path;
	WaitingRoomLayoutType type;
	CustomWaitingRoomDataStatus status;

	WaitingRoomBrandingPageColor page_color;
	WaitingRoomBrandingTextColor text_color;
	WaitingRoomBrandingButtonColor button_color;
}CustomWaitingRoomData;

/**
 * @class IWaitingRoomDataDownloadHandler
 * @brief handler for download waitingRoom Customize Data if download fail.
 */
class IWaitingRoomDataDownloadHandler
{
public:
	virtual ~IWaitingRoomDataDownloadHandler() {}
	
	/**
	 * @brief Retries downloading the waiting room customize data information in the waiting room.
	 * @return true if the retry succeeds. Otherwise, false.
	 */
	virtual bool Retry() = 0;
	
	/**
	 * @brief Ignores downloading the waiting room customize data information in the waiting room.
	 */
	virtual void Ignore() = 0;
};

/**
 * @class IMeetingWaitingRoomEvent
 * @brief Meeting Waiting Room Callback Event.
 */
class IMeetingWaitingRoomEvent
{
public:
	virtual ~IMeetingWaitingRoomEvent() {}
	
	/**
	 * @brief Callback event of notification that user joins the waiting room.
	 * @param userID The ID of user who joins the waiting room. 
	 */
	virtual void onWaitingRoomUserJoin(unsigned int userID) = 0;
	
	/**
	 * @brief Callback event of notification that user leaves the waiting room.
	 * @param userID The ID of user who leaves the waiting room.
	 */
	virtual void onWaitingRoomUserLeft(unsigned int userID) = 0;
	
	/**
	 * @brief Callback event triggered during the waiting room when the host changes audio status.	
	 * @param bAudioCanTurnOn true if audio can be turned on, false otherwise.
	 */
	virtual void onWaitingRoomPresetAudioStatusChanged(bool bAudioCanTurnOn) = 0;
	
	/**
	 * @brief Callback event triggered during the waiting room when the host changes video status.	
	 * @param bVideoCanTurnOn true if video can be turned on, false otherwise.
	 */
	virtual void onWaitingRoomPresetVideoStatusChanged( bool bVideoCanTurnOn) = 0;
	
	/**
	 * @brief During the waiting room, this callback event will be triggered when RequestCustomWaitingRoomData called.	
	 * @param The WaitingRoom Customize Data Info, handler for download waitingRoom Customize Data if download fail.
	 */
	virtual void onCustomWaitingRoomDataUpdated(CustomWaitingRoomData& bData, IWaitingRoomDataDownloadHandler* bHandler) = 0;
	
	/**
	 * @brief Callback indicating that the name of a user in the waiting room has changed.
	 * @param userID The ID of the user whose user name have has changed.
	 * @param userName The new user name.
	 */
	virtual void onWaitingRoomUserNameChanged(unsigned int userID, const zchar_t* userName) = 0;
	
	/**
	 * @brief This callback event will be triggered when host or cohost enables or disables waiting room entrance.
	 * @param bIsEnabled true enables waiting room entrance, false means disables waiting room entrance.
	 */
	virtual void onWaitingRoomEntranceEnabled(bool bIsEnabled) = 0;
};

/**
 * @class IMeetingWaitingRoomController
 * @brief Meeting waiting room controller interface.
 */
class IMeetingWaitingRoomController
{
public:
	/**
	 * @brief Sets meeting waiting room callback event handler.
	 * @param pEvent A pointer to the IMeetingWaitingRoomEvent that receives the waiting room event. 
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError SetEvent(IMeetingWaitingRoomEvent* pEvent) = 0;
	
	/**
	 * @brief Determines whether the current meeting supports the waiting room or not.
	 * @return true indicates to support.
	 */
	virtual bool IsSupportWaitingRoom() = 0;
	
	/**
	 * @brief Determines if the attendee is enabled to enter the waiting room when joining the meeting.
	 * @return true indicates to enable to enter.
	 */
	virtual bool IsWaitingRoomOnEntryFlagOn() = 0;
	
	/**
	 * @brief Sets to enable the attendee to enter the waiting room when joining the meeting.
	 * @param bEnable true indicates to enable to enter. false not.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError EnableWaitingRoomOnEntry(bool bEnable) = 0;
	
	/**
	 * @brief Gets the list of attendees who are in the waiting room.
	 * @return If the function succeeds, the return value is the list of attendees. Otherwise, this function returns nullptr. 
	 */
	virtual IList<unsigned int >* GetWaitingRoomLst() = 0;
	
	/**
	 * @brief Gets the attendee information in the waiting room via user ID.
	 * @param userid Specifies the user ID.
	 * @return If the function succeeds, the return value is a pointer to IUserInfo. Otherwise, this function returns nullptr.
	 */
	virtual IUserInfo* GetWaitingRoomUserInfoByID(unsigned int userid) = 0;
	
	/**
	 * @brief Permit the specified user to join the meeting.
	 * @param userid Specifies the user ID.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError AdmitToMeeting(unsigned int userid) = 0;
	
	/**
	 * @brief Permit all of the users currently in the waiting room to join the meeting.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError AdmitAllToMeeting() = 0;
	
	/**
	 * @brief Enables the specified user to enter the waiting room.
	 * @param userid Specifies the user ID.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError PutInWaitingRoom(unsigned int userid) = 0;
	
	/**
	 * @brief Determines if the attendee is enabled to turn on audio when joining the meeting.
	 * @return true indicates to enable to turn on.
	 */
	virtual bool IsAudioEnabledInWaitingRoom() = 0;
	
	/**
	 * @brief Determines if the attendee is enabled to turn on video when joining the meeting.
	 * @return true indicates to enable to turn on.
	 */
	virtual bool IsVideoEnabledInWaitingRoom() = 0;

	/**
     * @brief Pre-set audio mute or unmute status in waiting room.
     * @param bMute Pre-set audio mute or unmute status.
     * @return SDKERR_SUCCESS means the pre-set success, otherwise failed.
     */
	virtual SDKError PresetAudioInWaitingRoom(bool bMute) = 0;

	/**
	 * @brief Gets the audio pre-set mute or unmute status in waiting room.
	 * @return If pre-set audio unmuted, the return value is true, other the return value is false and pre set audio muted.
	 */
	virtual bool IsPresetAudioUnmuteInWaitingRoom() = 0;

	/**
	 * @brief Pre-set video mute or unmute status in waiting room.
	 * @param bMute Pre-set video mute or unmute status.
	 * @return SDKERR_SUCCESS means the pre-set success, otherwise failed.
	 */
	virtual SDKError PresetVideoInWaitingRoom(bool bMute) = 0;

	/**
	 * @brief Gets the video pre-set mute or unmute status in waiting room.
	 * @return true indicates pre-set unmute, false means pre-set mute.
	 */
	virtual bool IsPresetVideoUnmuteInWaitingRoom() = 0;

	/**
	 * @brief Gets the WaitingRoom CustomizeData information in the waiting room.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. See \link onCustomWaitingRoomDataUpdated \endlink to access the result data. Otherwise, this function returns an error.
	 */
	virtual SDKError RequestCustomWaitingRoomData() = 0;
	
	/**
	 * @brief Determines if the host or cohost can rename users in the waiting room.
	 * @param [out] bIsCan true indicates the host or cohost can rename users in the waiting room. Otherwise they can't.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError CanRenameUser(bool& bIsCan) = 0;
	
	/**
	 * @brief Change a user's screen name in the waiting room.
	 * @param userid The ID of users put into the waiting room by a host or cohost.
	 * @param userName The new user name.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError RenameUser(unsigned userid, const zchar_t* newName) = 0;
	
	/**
	 * @brief Determines if a host or cohost can expel user(s) in the waiting room.
	 * @param [out] bIsCan true indicates that a host or cohost can expel user(s) in the waiting room. Otherwise they may not
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError CanExpelUser(bool& bIsCan) = 0;
	
	/**
	 * @brief Remove a specified user from the waiting room.
	 * @param userid The ID of the user  removed from the waiting room by a host or cohost.
     * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError ExpelUser(unsigned int userid) = 0;
	
	/**
	 * @brief Determines if the enable waiting room on entry feature is locked, see \link EnableWaitingRoomOnEntry \endlink
	 * @return true if locked. Otherwise, false.
	 */
	virtual bool IsWaitingRoomOnEntryLocked() = 0;
};
END_ZOOM_SDK_NAMESPACE
#endif