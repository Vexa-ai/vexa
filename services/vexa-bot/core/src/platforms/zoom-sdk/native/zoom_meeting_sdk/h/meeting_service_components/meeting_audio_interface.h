/**
 * @file meeting_audio_interface.h
 * @brief Meeting Service Audio Interface. 
 */
#ifndef _MEETING_AUDIO_INTERFACE_H_
#define _MEETING_AUDIO_INTERFACE_H_
#include "zoom_sdk_def.h"

BEGIN_ZOOM_SDK_NAMESPACE
/**
 * @brief Enumeration of audio status of the user.
 * Here are more detailed structural descriptions.
 */
enum AudioStatus
{
	/** Initialization. */
	Audio_None,
	/** Muted status. */
	Audio_Muted,
	/** Unmuted status. */
	Audio_UnMuted,
	/** Muted by the host. */
	Audio_Muted_ByHost,
	/** Unmuted by the host. */
	Audio_UnMuted_ByHost,
	/** The host mutes all. */
	Audio_MutedAll_ByHost,
	/** The host unmutes all. */
	Audio_UnMutedAll_ByHost,
};
/**
 * @brief Enumeration of audio type of the user.
 * Here are more detailed structural descriptions.
 */
enum AudioType
{
	/** Normal audio type. */
	AUDIOTYPE_NONE,
	/** In VoIP mode. */
	AUDIOTYPE_VOIP,
	/** In telephone mode. */
	AUDIOTYPE_PHONE,
	/** Unknown mode. */
	AUDIOTYPE_UNKNOWN,
};
/**
 * @class IRequestStartAudioHandler
 * @brief Process after the user receives the requirement from the host to turn on the audio.
 */
class IRequestStartAudioHandler
{
public:
	virtual ~IRequestStartAudioHandler(){};
	/**
	 * @brief Gets the user ID who asks to turn on the audio.
	 * @return If the function succeeds, it returns the user ID. Otherwise, this function returns ZERO(0).
	 * @deprecated This interface is marked as deprecated.
	 */
	virtual unsigned int GetReqFromUserId() = 0;
	/**
	 * @brief Ignores the requirement, returns nothing and finally self-destroys.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError Ignore() = 0;
	/**
	 * @brief Accepts the requirement, turns on the audio and finally self-destroys.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError Accept() = 0;
	/**
	 * @brief Ignores the request to enable the audio in the meeting and finally the instance self-destroys.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError Cancel() = 0;
};

/**
 * @class IUserAudioStatus
 * @brief User audio status interface.
 */
class IUserAudioStatus
{
public:
	/**
	 * @brief Gets the user ID.
	 * @return If the function succeeds, it returns the user ID. Otherwise, this function returns ZERO(0).
	 */
	virtual unsigned int GetUserId() = 0;
	
	/**
	 * @brief Gets the audio status of the user.
	 * @return If the function succeeds, it returns the value defined in AudioStatus enum. Otherwise, this function returns an error.
	 */
	virtual AudioStatus GetStatus() = 0;
	
	/**
	 * @brief Gets the audio type of the user. 
	 * @return If the function succeeds, it returns the value defined in AudioType enum. Otherwise, this function returns an error.
	 */
	virtual AudioType   GetAudioType() = 0;
	virtual ~IUserAudioStatus(){};
};

/**
 * @class IMeetingAudioCtrlEvent
 * @brief Meeting audio callback event
 */
class IMeetingAudioCtrlEvent
{
public:
	/**
	 * @brief User's audio status changed callback.
	 * @param lstAudioStatusChange List of the user information with audio status changed. The list will be emptied once the function calls end. 
	 * @param strAudioStatusList List of the user information whose audio status changes, saved in json format. This parameter is currently invalid, hereby only for reservations. 
	 */
	virtual void onUserAudioStatusChange(IList<IUserAudioStatus* >* lstAudioStatusChange, const zchar_t* strAudioStatusList = nullptr) = 0;

	/**
	 * @brief The callback event that users whose audio is active changed.
	 * @param plstActiveAudio List to store the ID of user whose audio is active.
	 */
	virtual void onUserActiveAudioChange(IList<unsigned int >* plstActiveAudio) = 0;

	/**
	 * @brief Callback event of the requirement to turn on the audio from the host.
	 * @param handler_ A pointer to the IRequestStartAudioHandler.
	 */
	virtual void onHostRequestStartAudio(IRequestStartAudioHandler* handler_) = 0;

	/**
	 * @brief Callback event that requests to join third party telephony audio.
	 * @param audioInfo Instruction on how to join the meeting with third party audio.
	 */
	virtual void onJoin3rdPartyTelephonyAudio(const zchar_t* audioInfo) = 0;
	
	/**
	 * @brief Callback event for the mute on entry status change. 
	 * @param bEnabled Specify whether mute on entry is enabled or not.
	 */
	virtual void onMuteOnEntryStatusChange(bool bEnabled) = 0;

	virtual ~IMeetingAudioCtrlEvent() {}
};

/**
 * @class IMeetingAudioController
 * @brief Meeting audio controller interface.
 */
class IMeetingAudioController
{
public:
	/**
	 * @brief Configures the meeting audio controller callback event handler.
	 * @param pEvent An object pointer to the IMeetingAudioCtrlEvent that receives the meeting audio callback event.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 * @note The SDK uses pEvent to transmit the callback event to the user's application. If the function is not called or fails, the user's application is unable to retrieve the callback event.
	 */
	virtual SDKError SetEvent(IMeetingAudioCtrlEvent* pEvent) = 0;

	/**
	 * @brief Joins VoIP meeting.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 * @note Valid for both ZOOM style and user custom interface mode.
	 */
	virtual SDKError JoinVoip() = 0;

	/**
	 * @brief Leaves VoIP meeting.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 * @note Valid for both ZOOM style and user custom interface mode.
	 */
	virtual SDKError LeaveVoip() = 0;

	/**
	 * @brief Mutes the assigned user.
	 * @param userid The user ID to mute. ZERO(0) indicates to mute all the participants.
	 * @param allowUnmuteBySelf true if the user may unmute himself when everyone is muted, false otherwise.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 * @note Valid for both ZOOM style and user custom interface mode.
	 */
	virtual SDKError MuteAudio(unsigned int userid, bool allowUnmuteBySelf = true) = 0;

	/**
	 * @brief Unmutes the assigned user. 
	 * @param userid The user ID to unmute. 
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 * @note Valid for both ZOOM style and user custom interface mode.
	 */
	virtual SDKError UnMuteAudio(unsigned int userid) = 0;

	/**
	 * @brief Determines whether the user can unmute himself.
	 * @return true if the user can unmute himself. Otherwise, false.
	 * @note Valid for both ZOOM style and user custom interface mode.
	 */
	virtual bool CanUnMuteBySelf() = 0;

	/**
	 * @brief Determines whether the host or cohost can enable mute on entry.
	 * @return true if the host or cohost can enable mute on entry. Otherwise, false.
	 * @note Valid for both ZOOM style and user custom interface mode.
	 */
	virtual bool CanEnableMuteOnEntry() = 0;

	/**
	 * @brief Mutes or unmutes the user after joining the meeting. 
	 * @param bEnable true indicates to mute the user after joining the meeting.
	 * @param allowUnmuteBySelf true indicates to allow the user to unmute by self.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 * @note Valid for both ZOOM style and user custom interface mode.
	 */
	virtual SDKError EnableMuteOnEntry(bool bEnable,bool allowUnmuteBySelf) = 0;

	/**
	 * @brief Determines if mute on entry is enabled.
	 * @return true indicates that mute on entry is enabled. 
	 */
	virtual bool IsMuteOnEntryEnabled() = 0;

	/**
	 * @brief User joins or leaves the meeting in silence or no.
	 * @param bEnable true indicates to play chime when the user joins or leaves the meeting.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 * @note Valid for both ZOOM style and user custom interface mode.
	 */
	virtual SDKError EnablePlayChimeWhenEnterOrExit(bool bEnable) = 0;

	/**
	 * @brief Stops the incoming audio.
	 * @param bStop true indicates to stop the incoming audio. false not.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError StopIncomingAudio(bool bStop) = 0;

	/**
	 * @brief Determines if the incoming audio is stopped.
	 * @return true indicates that the incoming audio is stopped. 
	 */
	virtual bool IsIncomingAudioStopped() = 0;

	/**
	 * @brief Determines if the meeting has third party telephony audio enabled.
	 * @return true if enabled. Otherwise, false.
	 */
	virtual bool Is3rdPartyTelephonyAudioOn() = 0;

	/**
	 * @brief Enables or disables SDK to play meeting audio.
	 * @param bEnable true to enable SDK to play meeting audio, false to disable.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 * @note SDK will not support sharing computer sound when disabling playing meeting audio.
	 */
	virtual SDKError EnablePlayMeetingAudio(bool bEnable) = 0;

	/**
	 * @brief Determines if play meeting audio is enabled or not.
	 * @return true if enabled. Otherwise, false.
	 */
	virtual bool IsPlayMeetingAudioEnabled() = 0;
};
END_ZOOM_SDK_NAMESPACE
#endif