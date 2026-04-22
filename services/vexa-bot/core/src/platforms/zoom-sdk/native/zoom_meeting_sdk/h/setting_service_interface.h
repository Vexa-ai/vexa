/**
 * @file setting_service_interface.h
 * @brief Configure Service Interface. 
 */
#ifndef _SETTING_SERVICE_INTERFACE_H_
#define _SETTING_SERVICE_INTERFACE_H_
#include "zoom_sdk_def.h"
#if defined(WIN32)
#include "zoom_sdk_util_define.h"
#endif

BEGIN_ZOOM_SDK_NAMESPACE
/**
 * @class ICameraInfo
 * @brief Camera device information interface.
 */
class ICameraInfo
{
public:
    /**
	 * @brief Gets the camera device ID.
	 * @return If the function succeeds, the return value is the camera device ID. Otherwise returns nullptr.
	 */
	virtual const zchar_t* GetDeviceId() = 0;

    /**
	 * @brief Gets the camera device name.
	 * @return If the function succeeds, the return value is the camera device name. Otherwise returns nullptr.
	 */
	virtual const zchar_t* GetDeviceName() = 0;

    /**
	 * @brief Determines if the current device is selected to use.
	 * @return true indicates that the current device is selected.
	 */
	virtual bool IsSelectedDevice() = 0;

	virtual ~ICameraInfo() {};
};

/**
 * @class IMicInfo
 * @brief Microphone device information interface.
 */
class IMicInfo
{
public:
    /**
	 * @brief Gets the microphone device ID.
	 * @return If the function succeeds, the return value is the camera device ID. Otherwise returns nullptr.
	 */
	virtual const zchar_t* GetDeviceId() = 0;

    /**
	 * @brief Gets the microphone device name.
	 * @return If the function succeeds, the return value is the camera device name. Otherwise returns nullptr.
	 */
	virtual const zchar_t* GetDeviceName() = 0;

    /**
	 * @brief Determines if the current device is selected to use.
	 * @return true indicates that the current device is selected.
	 */
	virtual bool IsSelectedDevice() = 0;

	virtual ~IMicInfo() {};
};

/**
 * @class ISpeakerInfo
 * @brief Audio speaker device information interface.
 */
class ISpeakerInfo
{
public:
    /**
	 * @brief Gets the speaker device ID.
	 * @return If the function succeeds, the return value is the camera device ID. Otherwise returns nullptr.
	 */
	virtual const zchar_t* GetDeviceId() = 0;

    /**
	 * @brief Gets the speaker device name.
	 * @return If the function succeeds, the return value is speaker device name. Otherwise returns nullptr.
	 */
	virtual const zchar_t* GetDeviceName() = 0;

    /**
	 * @brief Determines if the current device is selected to use.
	 * @return true indicates that the current device is selected.
	 */
	virtual bool IsSelectedDevice() = 0;

	virtual ~ISpeakerInfo() {};
};

/**
 * @class IRingSpeakerInfo
 * @brief Ring speaker device information interface.
 */
class IRingSpeakerInfo
{
public:
    /**
	 * @brief Gets the ring speaker device ID.
	 * @return If the function succeeds, the return value is the ring speaker device ID. Otherwise returns nullptr.
	 */
	virtual const zchar_t* GetDeviceId() = 0;

    /**
	 * @brief Gets the ring speaker device name.
	 * @return If the function succeeds, the return value is ring speaker device name. Otherwise returns nullptr.
	 */
	virtual const zchar_t* GetDeviceName() = 0;

    /**
	 * @brief Determines if the current device is selected to use.
	 * @return true indicates that the current device is selected.
	 */
	virtual bool IsSelectedDevice() = 0;

	virtual ~IRingSpeakerInfo() {};
};

/**
 * @brief Enumeration of the values to limit fps.
 */
enum LimitFPSValue
{
    /** The feature is not enabled. */
	limitfps_Not_Enable,
    /** 1 frame per second. */
	limitfps_1_frame,
    /** 2 frame per second. */
	limitfps_2_frame,
    /** 4 frame per second. */
	limitfps_4_frame,
    /** 6 frame per second. */
	limitfps_6_frame,
    /** 8 frame per second. */
	limitfps_8_frame,
    /** 10 frame per second. */
	limitfps_10_frame,
    /** 15 frame per second. */
	limitfps_15_frame,
};


#if defined(WIN32)
/**
 * @brief Enumeration of the skin tone type. For more information, please visit <https://support.zoom.com/hc/en/article?id=zm_kb&sysparm_article=KB0063323>.
 */
typedef enum tagReactionSkinToneType
{
    /** Do not use any tone. */
	ReactionSkinTone_None = 0,
    /** default skin tone. */
	ReactionSkinTone_Default,
    /** light skin tone. */
	ReactionSkinTone_Light,
    /** medium light skin tone. */
	ReactionSkinTone_MediumLight,
    /** medium skin tone. */
	ReactionSkinTone_Medium,
    /** medium dark skin tone. */
	ReactionSkinTone_MediumDark,
    /** dark skin tone. */
	ReactionSkinTone_Dark,
}ReactionSkinToneType;

/**
 * @brief Enumeration of the UI theme. For more information, please visit <https://support.zoom.com/hc/en/article?id=zm_kb&sysparm_article=KB0061932>.
 */
enum ZoomSDKUITheme
{
    /** Use bloom theme. */
	ZoomSDKUITheme_Bloom,
    /** Use rose theme. */
	ZoomSDKUITheme_Rose,
    /** Use agave theme. */
	ZoomSDKUITheme_Agave,
    /** Use classic theme. */
	ZoomSDKUITheme_Classic,
};

/**
 * @brief Enumeration of the UI Appearance. For more information, please visit <https://support.zoom.com/hc/en/article?id=zm_kb&sysparm_article=KB0061932>.
 */
enum ZoomSDKUIAppearance
{
    /** Use the default, brighter theme. */
	ZoomSDKUIAppearance_Light = 1,
    /** Use the darker theme. */
	ZoomSDKUIAppearance_Dark,
    /** Follow the system settings for light or dark theme. */
	ZoomSDKUIAppearance_System,
};

/**
 * @brief Enumeration of the windows size when share.
 */
enum WindowSizeType
{
    /** For initialization. */
	WindowSize_None = 0,
    /** Full screen when share. */
	WindowSize_FullScreen,
    /** Maximize window when share. */
	WindowSize_Maximize,
    /** Current size when share. */
	WindowSize_CurrentSize,
};

/**
 * @brief Enumeration of the shown tab page at the top of the displayed setting dialog.
 */ 
enum SettingTabPage
{
    /** General setting page is on top. */
	SettingTabPage_General,
    /** Audio setting page is on top. */
	SettingTabPage_Audio,
    /** Video setting page is on top. */
	SettingTabPage_Video,
};

/**
 * @brief Enumeration of the mode for screen capture. For more information, please visit <https://support.zoom.com/hc/en/article?id=zm_kb&sysparm_article=KB0063824>.
 */
enum ScreenCaptureMode
{
    /** Automatically choose the best method to use for screen share. */
	CaptureMode_auto = 0,
    /** This mode can be applicable if you are not on the latest operating systems, or don't have certain video drivers. If this option isn't enabled, a blank screen may appear on participants' screens while the host shares their screen. */
	CaptureMode_legacy,
    /** This mode will share your screen without showing windows from the app. */
	CaptureMode_gpu_copy_filter,
    /** This mode will share your screen, include motion detection (when you move a window or play a movie), and will not show windows from the app. */
	CaptureMode_ada_copy_filter,
    /** This mode will share your screen, include motion detection (when you move a window or play a movie), and will show windows from the app. */
	CaptureMode_ada_copy_without_filter,
    /** When selected, only the content in the window is shared. */
	CaptureMode_ada_secure_filter,
    /** For end of enumeration*/
	CaptureMode_end,
};

/**
 * @brief Enumeration of screen sharing options when setting the page share screen item.  For more information, please visit <https://support.zoom.com/hc/en/article?id=zm_kb&sysparm_article=KB0060612>.
 */
enum ShareOptionInMeeting
{
    /** Show all sharing options. */
	ShareOptionInMeeting_AllOption,
    /** Automatically share desktop. */
	ShareOptionInMeeting_AutoShareDesktop,
};

/**
 * @brief Enumeration of screen sharing options when you share directly to a Zoom Room.  For more information, please visit <https://support.zoom.com/hc/en/article?id=zm_kb&sysparm_article=KB0060612>.
 */
enum ShareOptionToRoom
{
    /** Show all sharing options. */
	ShareOptionToRoom_AllOption,
    /** Automatically share desktop. */
	ShareOptionToRoom_AutoShareDesktop,
};

/**
 * @brief Enumeration of share select mode
 */
enum ShareSelectMode
{
    /** None. */
	SelectMode_None = 0,
    /** window mode. */
	SelectMode_Window,
    /** process mode. */
	SelectMode_Process,
};

/**
 * @brief The parameter to show the meeting setting dialog in Zoom UI.
 */
typedef struct tagShowSettingDlgParam
{
     /** Parent window handle. */
	HWND hParent;
     /** The Y-axis value of the top-left corner of the dialog uses the coordinate system of the monitor. */
	int top;
     /** The X-axis value of the top-left corner of the dialog uses the coordinate system of the monitor. */
	int left;
     /** Window handle of the dialog setting. */
	HWND hSettingWnd;
     /** Enable to display or not. */
	bool bShow;
     /** Enable to display the dialog at the center of the screen and discard the value of top and left. */
	bool bCenter;
     /** The tab page shown at the top of the displayed setting dialog. */
	SettingTabPage eTabPageType; 
	tagShowSettingDlgParam()
	{
		hParent = nullptr;
 		top = 0;
		left = 0;
		hSettingWnd = nullptr;
		bShow = true;
		bCenter = false;
		eTabPageType = SettingTabPage_General;
	}
}ShowSettingDlgParam;

/**
 * @brief Define the strategy to show the tab pages in the setting dialog.
 */
typedef struct tagSettingDlgShowTabPageOption
{
    /** true indicates to show general page. */
	bool bShowGeneral;
    /** true indicates to show video page. */
	bool bShowVideo; 
    /** true indicates to show audio page. */
	bool bShowAudio;
    /** true indicates to show share screen page. */
	bool bShowShareScreen; 
    /** true indicates to show virtual background page. */
	bool bShowVirtualBackGround;
    /** true indicates to show recording page. */
	bool bSHowRecording;
    /** true indicates to show profile page. */
	bool bShowAdvancedFeature;
    /** true indicates to show statistics page. */
	bool bShowStatistics;
    /** true indicates to show feed back page.This parameter is marked as deprecated. */
	bool bShowFeedback;
    /** true indicates to show keyboard shortcuts page. */
	bool bShowKeyboardShortcuts;
    /** true indicates to show accessibility page. */
	bool bShowAccessibility;
	tagSettingDlgShowTabPageOption()
	{
		bShowGeneral = true;
		bShowVideo = true;
		bShowAudio = true;
		bShowShareScreen = true;
		bShowVirtualBackGround = true;
		bSHowRecording = true;
		bShowStatistics = true;
		bShowAccessibility = true;
		bShowKeyboardShortcuts = true;
		bShowAdvancedFeature = false;
		bShowFeedback = false;
	}

}SettingDlgShowTabPageOption;

/**
 * @brief Define the strategy to show the url in the setting dialog.
 */
typedef struct tagSettingDlgShowUrlOption
{
    /** true indicates to the view more setting url in general page. */
	/** @deprecated This parameter is marked as deprecated.*/
	bool bShowGeneralViewMoreSetting;
    /** true indicates to show the support center url in video page. */
	bool bShowVideoSupportCenter; 
    /** true indicates to show the learn more url of suppress background noise in audio page. */
	bool bShowAudioLearnMore;
    /** true indicates to show the learn more url of screen capture mode in share screen and vb page. */
	bool bShowShareAndVBLearnMore;
	tagSettingDlgShowUrlOption()
	{
		bShowGeneralViewMoreSetting = true;
		bShowVideoSupportCenter = true;
		bShowAudioLearnMore = true;
		bShowShareAndVBLearnMore = true;
	}

}SettingDlgShowUrlOption;

/**
 * @brief Enumeration of the status of the mic when testing.
 */
typedef	enum
{
     /** Test the mic via TestMicStartRecording. It is useless to call TestMicStopTesting/TestMicPlayRecording in this status. */
	enuCanTest = 0,
     /** Test the mic via TestMicStopTesting/TestMicPlayRecording. It is useless to call TestMicStartRecording in this status. */
	enuMicRecording,
     /** Test the mic via TestMicStopTesting/TestMicPlayRecording. It is useless call TestMicStartRecording in this status. */
	enuCanPlay,
} SDK_TESTMIC_STATUS;

/**
 * @class ITestAudioDeviceHelperEvent
 * @brief Audio device testing callback events.
 */
class ITestAudioDeviceHelperEvent
{
public:
	virtual ~ITestAudioDeviceHelperEvent() {}

    /**
	 * @brief Notify the current mic or speaker volume when testing.
	 * @param MicVolume Specify the volume of the mic.
	 * @param SpkVolume Specify the volume of the speaker.
	 */
	virtual void OnMicSpkVolumeChanged(unsigned int MicVolume, unsigned int SpkVolume) = 0;

    /**
	 * @brief Notify that either mic device or speaker device is not found.
	 * @param bMicOrSpk true indicates no mic device. false no speaker device.
	 */
	virtual void OnNoAudioDeviceIsUseful(bool bMicOrSpk) = 0; 

    /**
	 * @brief Notify the mic status when testing.
	 * @param status The mic status.
	 * @param bHandled The SDK default logic won't handle the mic status the value is set to be true.
	 * @note The enuCanPlay status indicates that the SDK has recorded the microphone sound for the longest time (6 seconds). If the bHandled is not set to TURE hereby, the SDK will call TestMicStopTesting() itself. 
	 */
	virtual void OnTestMicStatusChanged(SDK_TESTMIC_STATUS status,bool& bHandled) = 0; 

    /**
	 * @brief Notify the user that a mic/speaker device is selected when testing. Then the SDK will close the mic/speaker testing. The user shall restart the test manually if he still wants to test.
	 */
	virtual void OnSelectedAudioDeviceIsChanged() = 0;
};

/**
 * @class ITestAudioDeviceHelper
 * @brief Audio device test interface.
 */
class ITestAudioDeviceHelper
{
public:
    /**
	 * @brief Audio device test callback handler. 
	 * @param pEvent A pointer to the ITestAudioDeviceHelperEvent that receives audio device test event. 
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 * @note Call the function before using any other interface of the same class.
	 */
	virtual SDKError SetEvent(ITestAudioDeviceHelperEvent* pEvent) = 0;

    /**
	 * @brief Starts to test the mic.
	 * @param deviceID Specify to test a mic device. If the param is a wrong mic ID, the SDK will return an error. Otherwise the SDK tests the specified device and sets it as selected. The SDK will test the default device if no parameter is input.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 * @note The function is disabled if no event handler is set.
	 */
	virtual SDKError TestMicStartRecording(const zchar_t* deviceID = nullptr) = 0;

    /**
	 * @brief Stops the mic test. The SDK will return an error if there is no mic test.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 * @note The function is disabled if no event handler is set.
	 */
	virtual SDKError TestMicStopTesting() = 0;

    /**
	 * @brief Play the mic recorded sound. If there is no mic testing, SDK return an error.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 * @note The function is disabled if no event handler is set.
	 */
	virtual SDKError TestMicPlayRecording() = 0;
	
    /**
	 * @brief Starts to test the speaker.
	 * @param deviceID Specify to test a speaker device. If the param is a wrong speaker ID, the SDK will return an error. Otherwise the SDK tests the specified device and sets it as selected. The SDK will test the default device if no parameter is input.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 * @note The function is disabled if no event handler is set.
	 */
	virtual SDKError TestSpeakerStartPlaying(const zchar_t* deviceID = nullptr) = 0;
	
    /**
	 * @brief Stops the speaker test. The SDK will return an error if there is no speaker test.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 * @note The function is disabled if no event handler is set.
	 */
	virtual SDKError TestSpeakerStopPlaying() = 0;
	
    /**
	 * @brief Sets the time interval for audio test. 
	 * @param timerInterval Usually the SDK sends the mic and speaker volumes every 200 ms by default via ITestAudioDeviceHelperEvent::OnMicSpkVolumeChanged(). 
	 * @note With this function, the user can modify the time interval instead of 200. The time interval varies only from 50 to 1000 in millisecond.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 * @note This interface will stop the mic/speaker test(if there is). It is suggested to call it before audio test.
	 */
	virtual SDKError SetTimerInterval(unsigned int timerInterval) = 0;
};
#endif

/**
 * @brief Enumeration of the level to suppress background noise. For more information, please visit <https://support.zoom.com/hc/en/article?id=zm_kb&sysparm_article=KB0059985>.
 */
enum Suppress_Background_Noise_Level
{
    /** For initialization. */
	Suppress_BGNoise_Level_None = 0,
    /** Automatically adjust noise suppression. */
	Suppress_BGNoise_Level_Auto,
    /** Low level suppression. Allows faint background. */
	Suppress_BGNoise_Level_Low,
    /** Medium level suppression. Filters out moderate noise like computer fan or desk taps. */
	Suppress_BGNoise_Level_Medium,
    /** High level suppression. Eliminates most background speech and persistent noise. */
	Suppress_BGNoise_Level_High,
};

/**
 * @class IGeneralSettingContext
 * @brief General setting interface.
 */
class IGeneralSettingContext
{
public:
    /**
	 * @brief Enables or disable to copy invite url automatically when meeting starts. 
	 * @param bEnable true indicates to copy invite url automatically.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError EnableAutoCopyInviteLink(bool bEnable) = 0;

    /**
	 * @brief Determines if it is able to automatically copy invite url when meeting starts is enabled.
	 * @return true indicates to enabled.
	 */
	virtual bool IsAutoCopyInviteLinkEnabled() = 0;

    /**
	 * @brief Enables or disable to stop user's video and audio when user's display is off or screen save begins. 
	 * @param bEnable true indicates to stop.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError EnableMuteWhenLockScreen(bool bEnable) = 0;

    /**
	 * @brief Determines if it is able to stop user's video and audio when user's display is off or screen save begins.
	 * @return true indicates to enabled.
	 */
	virtual bool IsMuteWhenLockScreenEnabled() = 0;
#if defined(WIN32)
    /**
	 * @brief Enables or disable dual screen mode.
	 * @param bEnable true indicates to set to enable the dual screen mode.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError EnableDualScreenMode(bool bEnable) = 0;

    /**
	 * @brief Determines if the dual screen mode is supported.
	 * @return true indicates to enable the dual screen mode.
	 */
	virtual bool IsDualScreenModeEnabled() = 0;

    /**
	 * @brief Enables or disable the aero mode when sharing the screen.
	 * @param bTurnoff true indicates to turn off the aero mode when sharing the screen.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 * @note The function can only be called in windows 7 environment.
	 */
	virtual SDKError TurnOffAeroModeInSharing(bool bTurnoff) = 0;

    /**
	 * @brief Determines if the aero mode is turned off when sharing the screen.
	 * @return true indicates to turn off the mode.
	 */
	virtual bool IsAeroModeInSharingTurnOff() = 0;

    /**
	 * @brief Enables or disable to enter the full screen video mode automatically when join meeting. 
	 * @param bEnable true indicates to enter the full screen video mode.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError EnableAutoFullScreenVideoWhenJoinMeeting(bool bEnable) = 0;

    /**
	 * @brief Determines if it is able to enter the full screen video mode automatically when join meeting.
	 * @return true indicates to enter the full screen video mode.
	 */
	virtual bool IsAutoFullScreenVideoWhenJoinMeetingEnabled() = 0;
	
    /**
	 * @brief Enables or disable to use the split screen mode, which enables the attendees to view the lectures or the gallery.
	 * @param bEnable true indicates to enter the split screen mode.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError EnableSplitScreenMode(bool bEnable) = 0;

    /**
	 * @brief Determines if the split screen mode is enabled.
	 * @return true indicates enabled.
	 */
	virtual bool IsSplitScreenModeEnabled() = 0;
	
    /**
	 * @brief Enable/Disable reminder window when user exits the meeting. Available only for normal attendees (non-host).
	 * @param bEnable true indicates to enable reminder window.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError EnableDisplayReminderWindowWhenExit(bool bEnable) = 0;
	
    /**
	 * @brief Determines if reminder window is enabled when user exits the meeting.
	 * @return true indicates enabled.
	 */
	virtual bool IsDisplayReminderWindowWhenExitEnabled() = 0;
	
    /**
	 * @brief Enable/Disable to show the elapsed time of the meeting.
	 * @param bEnable true indicates to show the elapsed time. false not.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError EnableShowMyMeetingElapseTime(bool bEnable) = 0;
	
    /**
	 * @brief Determines if showing elapsed time of the meeting is enabled.
	 * @return true indicates to show.
	 */
	virtual bool IsShowMyMeetingElapseTimeEnabled() = 0;

    /**
	 * @brief Sets the emoji reaction skin tone type.
	 * @param skinTone Specifies the skin tone type.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError SetReactionSkinTone(ReactionSkinToneType skinTone) = 0;

    /**
	 * @brief Gets the emoji reaction skin tone type.
	 * @return The return value is the emoji reaction skin tone type.
	 */
	virtual ReactionSkinToneType GetReactionSkinTone() = 0;

    /**
	 * @brief Determines if support set UI theme.
	 * @return true if to support. Otherwise, false.
	 */
	virtual bool IsSupportSetUITheme() = 0;

    /**
	 * @brief Sets the UI skin theme type.
	 * @param theme Specifies the skin theme type.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError SetUITheme(ZoomSDKUITheme theme) = 0;

    /**
	 * @brief Gets the UI skin theme type.
	 * @return The return value is the UI skin theme type.
	 */
	virtual ZoomSDKUITheme GetUITheme() = 0;

    /**
	 * @brief Determines if support set UI appearance.
	 * @return true indicates to support.
	 */
	virtual bool IsSupportSetUIAppearance() = 0;

    /**
	 * @brief Sets the UI appearance type.
	 * @param theme Specifies the UI appearance type.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError SetUIAppearance(ZoomSDKUIAppearance appearance) = 0;

    /**
	 * @brief Gets the UI appearance type.
	 * @return The return value is the UI appearance type.
	 */
	virtual ZoomSDKUIAppearance GetUIAppearance() = 0;

#endif
};
#if defined(WIN32)
/**
 * @brief Enumeration of rotation the video preview based on the current view. 
 */
typedef enum
{
    /** No rotation. */
	PREVIEW_VIDEO_ROTATION_ACTION_0,
    /** Rotate to the left. */
	PREVIEW_VIDEO_ROTATION_ACTION_CLOCK90,
    /** Rotate 180 degrees. */
	PREVIEW_VIDEO_ROTATION_ACTION_CLOCK180,
    /** Rotate to the right. */
	PREVIEW_VIDEO_ROTATION_ACTION_ANTI_CLOCK90
} PREVIEW_VIDEO_ROTATION_ACTION, *PPREVIEW_VIDEO_ROTATION_ACTION;

/**
 * @brief Enumeration of hardware acceleration. For more information, please visit <https://support.zoom.com/hc/en/article?id=zm_kb&sysparm_article=KB0066515>.
 */
typedef enum
{
     /** Utilizes hardware resources to improve rendering of received video feeds. */
	VIDEO_HARDWARE_ENCODE_RECEIVING = 0,
     /** Utilizes hardware resources to improve rendering of the video feed being sent out. */
	VIDEO_HARDWARE_ENCODE_SENDING,
     /** Utilizes hardware resources to improve rendering of the overall video feeds. */
	VIDEO_HARDWARE_ENCODE_PROCESSING,
}VIDEO_HARDWARE_ENCODE_TYPE;

/**
 * @class ITestVideoDeviceHelperEvent
 * @brief Video Device test callback event.
 */
class ITestVideoDeviceHelperEvent
{
public:
	virtual ~ITestVideoDeviceHelperEvent() {}

    /**
	 * @brief Notification of no camera device is found.
	 */
	virtual void OnNoVideoDeviceIsUseful() = 0;
	
    /**
	 * @brief Notification of a camera device is selected during the test, then the SDK will close the video testing. The user should restart the test manually if he wants to test.
	 */
	virtual void OnSelectedVideoDeviceIsChanged() = 0; 
	
    /**
	 * @brief Notification of there is no window handle or a wrong window handle is used. 
	 */
	virtual void OnNoWindowToShowPreview() = 0;
};

/**
 * @class ITestVideoDeviceHelper
 * @brief Video device test interface.
 */
class ITestVideoDeviceHelper
{
public:
    /**
	 * @brief Video device test callback event handler. 
	 * @param pEvent A pointer to the ITestVideoDeviceHelperEvent that receives video device test event. 
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 * @note Call the function before using any other interface of the same class.
	 */
	virtual SDKError SetEvent(ITestVideoDeviceHelperEvent* pEvent) = 0;
	
    /**
	 * @brief Sets the window and the rectangle to display the video preview.
	 * @param hParentWnd Specify the window to display the video preview.
	 * @param rc Specify a rectangle on the window to display the video preview. The default value is {0,0,0,0}, which means the whole client area of the window.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 * @note This function SHOULD only be called ONCE. Any redundant calling will return SDKERR_WRONG_USAGE.
	 */
	virtual SDKError SetVideoPreviewParentWnd(HWND hParentWnd, RECT rc = _SDK_TEST_VIDEO_INIT_RECT) = 0;
	
    /**
	 * @brief Starts to test the camera. 
	 * @param deviceID Specify a camera device to test. If the param is a wrong camera ID, SDK returns an error. Otherwise SDK tests the specified device and sets it to be the selected one.
	 * @note The SDK will test the default device if no parameter is input.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 * @note The function can not work if no event is set or no window handle is set.
	 */
	virtual SDKError TestVideoStartPreview(const zchar_t* deviceID = nullptr) = 0;
	
    /**
	 * @brief Stops testing the camera.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 * @note The function can not work if there is no event or window handle to be set.
	 */
	virtual SDKError TestVideoStopPreview() = 0;
	
    /**
	 * @brief Rotate the video preview.
	 * @param action Specify the action to rotate the video.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 * @note The function can not work if there is no event or window handle to be set.  It works also in the meeting video when you rotate the preview video. Please use it with cautions.
	 */
	virtual SDKError TestVideoRotate(PREVIEW_VIDEO_ROTATION_ACTION action) = 0;	

	virtual ICameraController* GetTestCameraController() = 0;
};

/**
 * @class ILipSyncAvatarPreviewHelperEvent
 * @brief lip-sync avatar callback event.
 */
class ILipSyncAvatarPreviewHelperEvent
{
public:
	virtual ~ILipSyncAvatarPreviewHelperEvent() {}

    /**
	 * @brief Notification of there is no window handle or a wrong window handle is used. 
	 */
	virtual void OnNoWindowToShowLipsyncPreview() = 0;
};

/**
 * @class ILipSyncAvatarPreviewHelper
 * @brief lip-sync avatar preview helper interface.
 */
class ILipSyncAvatarPreviewHelper
{
public:
    /**
	 * @brief Video device test callback event handler. 
	 * @param pEvent A pointer to the ILipSyncAvatarPreviewHelperEvent that receives lip sync preview events. 
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 * @note Call the function before using any other interface of the same class.
	 */
	virtual SDKError SetEvent(ILipSyncAvatarPreviewHelperEvent* pEvent) = 0;

    /**
	 * @brief Sets the window and the rectangle to display the lip sync avatar preview.
	 * @param hParentWnd Specify the window to display lip-sync avatar preview.
	 * @param rc Specify a rectangle on the window to display the lip-sync avatar preview. The default value is {0,0,0,0}, which means the whole client area of the window.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 * @note This function SHOULD only be called ONCE. Any redundant calling will return SDKERR_WRONG_USAGE.
	 */
	virtual SDKError SetLipSyncAvatarPreviewParentWnd(HWND hParentWnd, RECT rc = _SDK_TEST_VIDEO_INIT_RECT) = 0;

    /**
	 * @brief Starts to preview lip sync avatar. 
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 * @note The function can not work if no event is set or no window handle is set.
	 */
	virtual SDKError StartLipSyncAvatarPreview() = 0;

    /**
	 * @brief Stops to preview lip-sync avatar.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 * @note The function can not work if there is no event or window handle to be set.
	 */
	virtual SDKError StopLipSyncAvatarPreview() = 0;

	virtual ~ILipSyncAvatarPreviewHelper() {};
};

/**
 * @class I3DAvatarImageInfo
 * @brief 3D avatar image information interface.
 */
class I3DAvatarImageInfo
{
public:
    /**
	 * @brief Determines if the current image is being used.
	 * @return true indicates that the current image is used as the 3D avatar image.
	 */
	virtual bool IsSelected() = 0;

    /**
	 * @brief Determines if the current item is most recently used.
	 * @return true indicates that the current image is the most recently used as the 3D avatar image.
	 */
	virtual bool IsLastUsed() = 0;

    /**
	 * @brief Gets the file path of the current image.
	 * @return If the function succeeds, the return value is the file path of current image. Otherwise returns nullptr.
	 */
	virtual const zchar_t* GetImageFilePath() = 0;

    /**
	 * @brief Gets the name of the current image.
	 * @return If the function succeeds, the return value is the name of the current image. Otherwise returns nullptr.
	 */
	virtual const zchar_t* GetImageName() = 0;

    /**
	 * @brief Gets the index of the current image.
	 * @return If the function succeeds, it returns the index of current image.
	 * @note If no image is selected, the index value will be -1.
	 */
	virtual int GetIndex() = 0;

	virtual ~I3DAvatarImageInfo() {};
};

#endif
/**
 * @brief Enumeration of light adaption type.
 */
typedef enum 
{
     /** None. */
	Light_Adaption_None = 0,
    /** Auto type. */
	Light_Adaption_Auto,
    /** Manual type. */
	Light_Adaption_Manual,
}VIDEO_LIGHT_ADAPTION_TYPE;


/**
 * @class IVideoSettingContextEvent
 * @brief Video setting context callback event.
 */
class IVideoSettingContextEvent
{
public:
	virtual ~IVideoSettingContextEvent() {}

    /**
	 * @brief Callback event if the SDK detects that the computer camera devices have been changed.
	 * @param pNewCameraList The new list of all camera devices plugged into the computer.
	 */
	virtual void onComputerCamDeviceChanged(IList<ICameraInfo*>* pNewCameraList) = 0;

    /**
	 * @brief Notify the user that a camera device is selected.
	 * @param deviceId Specify a device to be selected.
	 * @param deviceName Specify the device name assigned by deviceId.
	 */
	virtual void onDefaultCamDeviceChanged(const zchar_t* deviceId, const zchar_t* deviceName) = 0;
};

/**
 * @brief Enumeration of the auto framing modes in video. 
 */
enum AutoFramingMode
{
    /** No auto framing. */
	AutoFramingMode_none,
    /** Use the video frame's center point as the center to zoom in. */
	AutoFramingMode_center_coordinates,
    /** Use the detected face in the video frame as the center to zoom in. */
	AutoFramingMode_face_recognition,
};

/**
 * @brief Enumeration of the face recognition failure strategies.
 */
enum FaceRecognitionFailStrategy
{
    /** No use of the fail strategy. */
	FaceRecognitionFailStrategy_none,
    /** After face recognition fails, do nothing until face recognition succeeds again. */
	FaceRecognitionFailStrategy_remain,
    /** After face recognition fails, use the video frame's center point as the center for zoom in. */
	FaceRecognitionFailStrategy_using_center_coordinates,
    /** After face recognition fails, use original video. */
	FaceRecognitionFailStrategy_using_original_video,
};

/**
 * @brief Auto framing parameters.
 */
struct AutoFramingParameter
{
    /** The zoom in ratio of auto-framing, valid range of values: 1~10(when mode is AutoFramingMode_center_coordinates), 0.1~10(when mode is AutoFramingMode_face_recognition). */
	float ratio;
    /** Only mode is AutoFramingMode_face_recognition, the param is valid. */
	FaceRecognitionFailStrategy fail_Strategy;

	AutoFramingParameter()
	{
		ratio = 1;
		fail_Strategy = FaceRecognitionFailStrategy_using_original_video;
	}
};

/**
 * @class IVideoSettingContext
 * @brief Video setting interface.
 */
class IVideoSettingContext
{
public:
    /**
	 * @brief Gets camera device list.
	 * @return If the function succeeds, the return value the is camera device list. Otherwise returns nullptr.
	 */
	virtual IList<ICameraInfo* >* GetCameraList() = 0;

    /**
	 * @brief Select camera device.
	 * @param deviceId Specify a device to be selected.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError SelectCamera(const zchar_t* deviceId) = 0;

    /**
	 * @brief Enables or disable the video facial beauty effect.
	 * @param bEnable true indicates to enable the video facial beauty effect.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError EnableFaceBeautyEffect(bool bEnable) = 0;

    /**
	 * @brief Gets the flag to enable/disable the video facial beauty effect.
	 * @return true indicates enabled.
	 */
	virtual bool IsFaceBeautyEffectEnabled() = 0;

    /**
	 * @brief Gets the video facial beauty strength value.
	 * @return The video facial beauty strength value. If the video facial beauty effect is disabled, the return value is 0.
	 */
	virtual unsigned int GetFaceBeautyStrengthValue() = 0;

    /**
	 * @brief Sets the video facial beauty strength value.
	 * @param beautyStrengthValue The value is only effective when the video facial beauty effect is enabled. The value should between 0 to 100. 
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError SetFaceBeautyStrengthValue(unsigned int beautyStrengthValue) = 0;

    /**
	 * @brief Enables or disable the light adaption of the video.
	 * @param bEnable true indicates to enable the light adaption of the video.
	 * @param lightAdaptionType true indicates the  type to adjust the low light. If bEnable is true, the default value of lightAdaptionType is Light_Adaption_Auto.
	 * @param manualValue The value is only effective when the bAutoAdaption is false. The value should between 0 to 100. 
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError EnableLightAdaption(bool bEnable, VIDEO_LIGHT_ADAPTION_TYPE lightAdaptionType, double manualValue) = 0;

    /**
	 * @brief Gets the flag to enable/disable the light adaption of the video.
	 * @return true indicates enabled.
	 */
	virtual bool IsLightAdaptionEnabled() = 0;

    /**
	 * @brief Gets the light adaption type of the video.
	 * @return The light adaption type. If the light adaption is disabled, the return value is Light_Adaption_None.
	 */
	virtual VIDEO_LIGHT_ADAPTION_TYPE GetLightAdaptionType() = 0;

    /**
	 * @brief Gets the manual setting value for the light adaption of the video.
	 * @return The manual setting value. If the light adaption is disabled or the type of light adaption is AUTO, the return value is 0.
	 */
	virtual double GetLightAdaptionManualValue() = 0;

    /**
	 * @brief Enables or disable HD video.
	 * @param bEnable true indicates to enable the HD video.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 */
	virtual SDKError EnableHDVideo(bool bEnable) = 0;

    /**
	 * @brief Gets the flag to enable/disable the HD video.
	 * @return true indicates enabled.
	 */
	virtual bool IsHDVideoEnabled() = 0;

    /**
	 * @brief Enables or disable always use original size video.
	 * @param bEnable true indicates to enable always use original size video.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 */
	virtual SDKError EnableAlwaysUseOriginalSizeVideo(bool bEnable) = 0;

    /**
	 * @brief Gets the flag to enable/disable always use original size video.
	 * @return true indicates to use.
	 */
	virtual bool IsAlwaysUseOriginalSizeVideo() = 0;

    /**
	 * @brief Enables or disable video de-noise.
	 * @param bEnable true indicates to enable video de-noise.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 */
	virtual SDKError EnableTemporalDeNoise(bool bEnable) = 0;

    /**
	 * @brief Gets the flag to enable/disable the video de-noise.
	 * @return true indicates enabled.
	 */
	virtual bool IsTemporalDeNoiseEnabled() = 0;

    /**
	 * @brief Enables or disable to show the username on the video.
	 * @param bEnable true indicates to show the username on the video.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 */
	virtual SDKError EnableAlwaysShowNameOnVideo(bool bEnable) = 0;

    /**
	 * @brief Gets the flag to enable/disable to show the username on video.
	 * @return true indicates enabled.
	 */
	virtual bool IsAlwaysShowNameOnVideoEnabled() = 0;

    /**
	 * @brief Enables or disable to turn off the video when join meeting
	 * @param bEnable true indicates to enable to turn off the video when join meeting
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 */
	virtual SDKError EnableAutoTurnOffVideoWhenJoinMeeting(bool bEnable) = 0;

    /**
	 * @brief Gets the flag to enable to turn off the video when join meeting.
	 * @return true indicates enabled.
	 */
	virtual bool IsAutoTurnOffVideoWhenJoinMeetingEnabled() = 0;

    /**
	 * @brief Video device monitor callback event. 
	 * @param pEvent A pointer to the IVideoSettingContextEvent.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 *@remarks You must call the function if you want to monitor the video device plugged in/out.
	 */
	virtual SDKError SetVideoDeviceEvent(IVideoSettingContextEvent* pEvent) = 0;

    /**
	 * @brief Enables my video auto-framing.
	 * @param mode the auto-framing mode.
	 * @param param the auto-framing parameter.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError EnableVideoAutoFraming(AutoFramingMode mode, AutoFramingParameter& param) = 0;	

    /**
	 * @brief Determines whether auto-framing is enabled.
	 * @return true indicates enabled.
	 */
	virtual bool IsVideoAutoFramingEnabled() = 0;

    /**
	 * @brief Gets current mode of auto-framing.
	 * @param mode the auto-framing mode.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 */
	virtual SDKError GetVideoAutoFramingMode(AutoFramingMode& mode) = 0;

    /**
	 * @brief Sets the mode of auto-framing when auto-framing is enabled.
	 * @param mode the auto-framing mode.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 */
	virtual SDKError SetVideoAutoFramingMode(AutoFramingMode mode) = 0;

    /**
	 * @brief Sets the zoom in ratio of auto-framing when auto-framing is enabled.
	 * @param ratio the zoom in ratio of auto-framing, valid range of ratio: 
	 *        a. mode is "AutoFramingMode_center_coordinates", 1~10
	 *        b. mode is "AutoFramingMode_face_recognition", 0.1~10
   	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 */
	virtual SDKError SetVideoAutoFramingRatio(float ratio) = 0;

    /**
	 * @brief Sets the fail strategy of face recognition when auto-framing is enabled(mode is "AutoFramingMode_face_recognition")
	 * @param strategy the fail strategy of face recognition.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 */
	virtual SDKError SetFaceRecognitionFailStrategy(FaceRecognitionFailStrategy strategy) = 0;

    /**
	 * @brief Gets the setting of auto-framing.
	 * @param mode the auto-framing mode.
	 * @param param the auto-framing parameter.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 */
	virtual SDKError GetVideoAutoFramingSetting(AutoFramingMode mode, AutoFramingParameter& param) = 0;

    /**
	 * @brief Stops video auto-framing.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 */
	virtual SDKError DisableVideoAutoFraming() = 0;

    /**
	 * @brief Enables or disable optimizing received video quality when facing network issues for a variety of reasons.
	 * @note Applies to the window in focus: speaker view, pinned / spotlighted videos, gallery view with a small number of videos.
	 * @param bEnable true indicates to enable this feature. Otherwise disable this feature.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 */
	virtual SDKError EnableOptimizeVideoQuality(bool bEnable) = 0;

    /**
	 * @brief Determines if optimizing received video quality is enabled.
	 * @return true if that optimization is enabled. Otherwise, false.
	 */
	virtual bool IsOptimizeVideoQualityEnabled() = 0;

    /**
	 * @brief Determines if optimizing received video quality is supported.
	 * @return true indicates that it is supported.
	 */
	virtual bool IsOptimizeVideoQualitySupported() = 0;
#if defined(WIN32)
    /**
	 * @brief Enables or disable video mirror effect.
	 * @param bEnable true indicates to enable the video mirror effect.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 */
	virtual SDKError EnableVideoMirrorEffect(bool bEnable) = 0;

    /**
	 * @brief Checks whether the video mirror effect is enabled.
	 * @return true indicates enabled.
	 */
	virtual bool IsVideoMirrorEffectEnabled() = 0;

    /**
	 * @brief Enables or disable to spotlight the video.
	 * @param bEnable true indicates to enable to spotlight the video.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 */
	virtual SDKError EnableSpotlightSelf(bool bEnable) = 0;

    /**
	 * @brief Gets the flag to enable/disable to spotlight video.
	 * @return true indicates enabled.
	 */
	virtual bool IsSpotlightSelfEnabled() = 0;

    /**
	 * @brief Enables or disable the hardware acceleration.
	 * @param bEnable true indicates to enable the hardware acceleration.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 */
	virtual SDKError EnableHardwareEncode(bool bEnable, VIDEO_HARDWARE_ENCODE_TYPE encodeType) = 0;

    /**
	 * @brief Gets the flag to enable/disable the hardware acceleration.
	 * @return true indicates enabled.
	 */
	virtual bool IsHardwareEncodeEnabled(VIDEO_HARDWARE_ENCODE_TYPE encodeType) = 0;

    /**
	 * @brief Enables or disable to show the participants in Gallery View up to 49 per screen.
	 * @param bEnable true indicates to show the participants in Gallery View up to 49 per screen.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 */
	virtual SDKError Enable49VideoesInGallaryView(bool bEnable) = 0;

    /**
	 * @brief Gets the flag to enable/disable to show the participants in Gallery View up to 49 per screen.
	 * @return true indicates enabled.
	 */
	virtual bool Is49VideoesInGallaryViewEnabled() = 0;
	
    /**
	 * @brief Enables or disable to hide the non-video participants.
	 * @param bEnable true indicates to hide the non-video Participants.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 */
	virtual SDKError EnableHideNoVideoUsersOnWallView(bool bEnable) = 0;

    /**
	 * @brief Gets the flag to enable/disable to hide non-video participants.
	 * @return true indicates enabled.
	 */
	virtual bool IsHideNoVideoUsersOnWallViewEnabled() = 0;

    /**
	 * @brief Enables or disable to show the video preview dialog when join meeting
	 * @param bEnable true indicates to enable to show the video preview dialog when join meeting
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 */
	virtual SDKError EnableVideoPreviewDialog(bool bEnable) = 0;

    /**
	 * @brief Gets the flag to enable to show the video preview dialog when join meeting.
	 * @return true indicates enabled.
	 */
	virtual bool IsVideoPreviewDialogEnabled() = 0;

    /**
	 * @brief Enables or disable to stop incoming video.
	 * @param bEnable true indicates to enable to stop incoming video.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 */
	virtual SDKError EnableStopIncomingVideo(bool bEnable) = 0;

    /**
	 * @brief Gets the flag to enable/disable to stop in coming video.
	 * @return true indicates enabled.
	 */
	virtual bool IsStopIncomingVideoEnabled() = 0;

    /**
	 * @brief Enables or disables hiding the user's self view.
	 * @param bEnable true indicates to enable to hide the user's self view.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 * @note Valid only for Zoom style user interface mode.
	 */
	virtual SDKError EnableHideSelfView(bool bEnable) = 0;

    /**
	 * @brief Gets the flag to enable/disable to hide user self's view.
	 * @param[out] bEnabled true indicates enabled.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 * @note Valid only for Zoom style user interface mode.
	 */
	virtual SDKError IsHideSelfViewEnabled(bool& bEnabled) = 0;
	
    /**
	 * @brief Gets the pointer to ITestVideoDeviceHelper which is used to test camera device.
	 * @return If the function succeeds, the return value is the pointer to ITestVideoDeviceHelper. Otherwise returns nullptr.
	 */
	virtual ITestVideoDeviceHelper* GetTestVideoDeviceHelper() = 0;
#endif
};

/**
 * @class IAudioSettingContextEvent
 * @brief Audio setting context callback event.
 */
class IAudioSettingContextEvent
{
public:
	virtual ~IAudioSettingContextEvent() {}

    /**
	 * @brief Notification of the SDK detects that the computer mic devices have been changed.
	 * @param pNewMicList The new list of all mic devices plugged into the computer.
	 */
	virtual void onComputerMicDeviceChanged(IList<IMicInfo*>* pNewMicList) = 0;
	
    /**
	 * @brief Notification of the SDK detects that the computer speaker devices have been changed.
	 * @param pNewSpeakerList The new list of all speaker devices plugged into the computer. 
	 */
	virtual void onComputerSpeakerDeviceChanged(IList<ISpeakerInfo*>* pNewSpeakerList) = 0;

    /**
	 * @brief Notification of the user that a microphone device is selected.
	 * @param deviceId Specify a device to be selected.
	 * @param deviceName Specify the device name assigned by deviceId.
	 */
	virtual void onDefaultMicDeviceChanged(const zchar_t* deviceId, const zchar_t* deviceName) = 0;

    /**
	 * @brief Notification of the user that a speaker device is selected.
	 * @param deviceId Specify a device to be selected.
	 * @param deviceName Specify the device name assigned by deviceId.
	 */
	virtual void onDefaultSpeakerDeviceChanged(const zchar_t* deviceId, const zchar_t* deviceName) = 0;
};

/**
 * @brief Enumeration of signal processing by Windows audio device drivers. For more information, please visit <https://support.zoom.com/hc/en/article?id=zm_kb&sysparm_article=KB0066398>. 
 */
typedef enum
{
    /** Allows Zoom Workplace to set the Windows signal processing mode for your device. */
	SDK_AUDIO_DEVICE_RAW_MODE_DEFAULT,
    /** Enables the drivers to provide a level of audio processing. */
	SDK_AUDIO_DEVICE_RAW_MODE_ON, 
    /** Puts the drivers into "Raw" mode so that the ZoomWorkplave app can receive an unprocessed signal. */
	SDK_AUDIO_DEVICE_RAW_MODE_OFF 
}SDK_AUDIO_DEVICE_RAW_MODE_TYPE;

/**
 * @brief Enumeration for echo cancellation.  For more information, please visit <https://support.zoom.com/hc/en/article?id=zm_kb&sysparm_article=KB0066398>. 
 */
typedef enum {
    /** Automatically adjust echo cancellation, balancing CPU and performance. */
	SDK_ECHO_CANCELLATION_DEFAULT = 0,
    /** Better echo limitation, taking into account multiple people talking at the same time, low CPU utilization. */
	SDK_ECHO_CANCELLATION_LOW,
	/** Best experience when multiple people are talking at the same time. Enabling this option may increase CPU utilization. */
	SDK_ECHO_CANCELLATION_HIGH
}SDK_ECHO_CANCELLATION_LEVEL;

/**
 * @class IAudioSettingContext
 * @brief Audio setting interface.
 */
class IAudioSettingContext
{
public:
    /**
	 * @brief Gets the mic device list.
	 * @return If the function succeeds, the return value is the camera device list. Otherwise returns nullptr.
	 */
	virtual IList<IMicInfo* >* GetMicList() = 0;

    /**
	 * @brief Select mic device.
	 * @param deviceId Specify the device to be selected.
	 * @param deviceName Specify the device name assigned by deviceId.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 */
	virtual SDKError SelectMic(const zchar_t* deviceId, const zchar_t* deviceName) = 0;

    /**
	 * @brief Gets the speaker device list.
	 * @return If the function succeeds, the return value is the camera device list. Otherwise returns nullptr.
	 */
	virtual IList<ISpeakerInfo* >* GetSpeakerList() = 0;

    /**
	 * @brief Select speaker device.
	 * @param deviceId Specify the device to be selected.
	 * @param deviceName Specify the device the device name assigned by deviceId.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 */
	virtual SDKError SelectSpeaker(const zchar_t* deviceId, const zchar_t* deviceName) = 0;

    /**
	 * @brief Enables or disable the audio automatically when join meeting.
	 * @param bEnable true indicates to enable the audio automatically when join meeting.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 */
	virtual SDKError EnableAutoJoinAudio(bool bEnable) = 0;

    /**
	 * @brief Gets the flag to enable/disable the audio automatically when join meeting.
	 * @return true indicates that this is enabled.
	 */
	virtual bool IsAutoJoinAudioEnabled() = 0;

    /**
	 * @brief Enables or disable the auto-adjust mic volume.
	 * @param bEnable true indicates to enable to auto-adjust the mic volume.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 */
	virtual SDKError EnableAutoAdjustMic(bool bEnable) = 0;

    /**
	 * @brief Gets the flag to enable/disable to auto-adjust the mic volume.
	 * @return true indicates that this is enabled.
	 */
	virtual bool IsAutoAdjustMicEnabled() = 0;

    /**
	 * @brief Enables or disable to mute always the mic when join the meeting by VoiP.
	 * @param bEnable true indicates to enable to mute always the mic when join the meeting by VoiP.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 */
	virtual SDKError EnableAlwaysMuteMicWhenJoinVoip(bool bEnable) = 0;

    /**
	 * @brief Gets the flag to enable/disable to mute always the mic when join the meeting by VoiP.
	 * @return true indicates that this is enabled.
	 */
	virtual bool IsAlwaysMuteMicWhenJoinVoipEnabled() = 0;

    /**
	 * @brief Enables or disable to prompt when the user joins the meeting using the third party audio.
	 * @param bEnable true indicates to enable to prompt.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 */
	virtual SDKError EnableSuppressAudioNotify(bool bEnable) = 0;

    /**
	 * @brief Gets the flag to enable/disable to prompt when the user joins the meeting using the third party audio.
	 * @return true indicates that this is enabled.
	 */
	virtual bool IsSuppressAudioNotifyEnabled() = 0;

    /**
	 * @brief Sets the volume of the selected mic.
	 * @param value Specify the volume of the mic that varies between 0 and 255.
	 * @note The SDK will enable the default mic if there is no mic selected via SelectMic().
	 */
	virtual SDKError	SetMicVol(FLOAT& value) = 0;
	
    /**
	 * @brief Gets the volume of the selected mic.
	 * @param value [out] Specify the current volume of the mic.
	 */
	virtual SDKError    GetMicVol(FLOAT& value) = 0;
	
    /**
	 * @brief Sets the volume of the selected speaker.
	 * @param value Specify the volume of the speaker that varies between 0 and 255.
	 * @note The SDK will enable the default speaker if there is no speaker selected via SelectSpeaker.
	 */
	virtual SDKError	SetSpeakerVol(FLOAT& value) = 0;
	
    /**
	 * @brief Gets the volume of the selected speaker.
	 * @param value [out] Specify the current volume of the speaker.
	 */
	virtual SDKError    GetSpeakerVol(FLOAT& value) = 0;

    /**
	 * @brief Audio device monitor callback event. 
	 * @param pEvent A pointer to the IAudioSettingContextEvent that receives audio device plugged in/out event.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.   
	 * @note You must call the function if you want to monitor the audio device plugged in/out.
	 */
	virtual SDKError SetAudioDeviceEvent(IAudioSettingContextEvent* pEvent) = 0;

    /**
	 * @brief Gets the echo cancellation level.
	 * @return The the echo cancellation level.
	 */
	virtual SDK_ECHO_CANCELLATION_LEVEL GetEchoCancellationLevel() = 0;

    /**
	 * @brief Sets the echo cancellation level.
	 * @param level The new echo cancellation level to be set.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 */
	virtual SDKError SetEchoCancellationLevel(SDK_ECHO_CANCELLATION_LEVEL level) = 0;

    /**
	 * @brief Gets the suppress background noise level.
	 * @return The suppress background noise level.
	 */
	virtual Suppress_Background_Noise_Level GetSuppressBackgroundNoiseLevel() = 0;

    /**
	 * @brief Sets the suppress background noise level.
	 * @param level The new suppress background noise level to be set.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 */
	virtual SDKError SetSuppressBackgroundNoiseLevel(Suppress_Background_Noise_Level level) = 0;

    /**
	 * @brief Gets the audio device raw mode type.
	 * @return The audio device raw mode type.
	 */
	virtual SDK_AUDIO_DEVICE_RAW_MODE_TYPE GetAudioSignalProcessType() = 0;

    /**
	 * @brief Sets the audio device raw mode type.
	 * @param type The new audio device raw mode type to be set.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 */
	virtual SDKError SetAudioSignalProcessType(SDK_AUDIO_DEVICE_RAW_MODE_TYPE type) = 0;

    /**
	 * @brief Sets whether to disable the function of echo cancellation or not. 
	 * @param bDisable true indicates to disable the function, false not.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 * @note This function is valid only if mic original input is enabled, otherwise invalid.
	 */
	virtual SDKError DisableEchoCancellation(bool bDisable) = 0;

    /**
	 * @brief Checks whether the echo cancellation is disabled or not.
	 * @return If it is true, it means the echo cancellation is disabled
	 */
	virtual bool IsEchoCancellationDisabled() = 0;

	virtual IList<IRingSpeakerInfo* >* GetRingSpkList() = 0;

	virtual SDKError GetRingSpkVolume(FLOAT& fValue) = 0;

	virtual SDKError SetRingSpkVolume(FLOAT fValue) = 0;

	virtual SDKError SetRingSpkDevice(const zchar_t* spk_id) = 0;

	virtual SDKError UseDefaultSystemMic() = 0;

	virtual SDKError UseDefaultSystemSpeaker() = 0;

    /**
	 * @brief Enables or disable the original input of mic.
	 * @param bEnable true indicates to enable the original input of mic.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 */
	virtual SDKError EnableMicOriginalInput(bool bEnable) = 0;
#if defined(WIN32)
    /**
	 * @brief Enables or disable the stereo audio.
	 * @param bEnable true indicates to enable the stereo audio.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 * @note This function is valid only if mic original input is enabled, otherwise invalid.
	 */
	virtual SDKError EnableStereoAudio(bool bEnable) = 0;

    /**
	 * @brief Gets the flag to enable/disable the stereo audio.
	 * @return true indicates that this is enabled.
	 */
	virtual bool IsStereoAudioEnable() = 0;

    /**
	 * @brief Gets the flag to enable/disable the original input of mic.
	 * @return true indicates that this is enabled.
	 */
	virtual bool IsMicOriginalInputEnable() = 0;
	
    /**
	 * @brief Enables or disable to press and hold the Space-bar to speak when muted.
	 * @param bEnable true indicates to press and hold the Space-bar to speak. 
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 */
	virtual SDKError EnableHoldSpaceKeyToSpeak(bool bEnable) = 0;

    /**
	 * @brief Gets the flag to enable/disable to press and hold the Space-bar to speak.
	 * @return true indicates that this is enabled.
	 */
	virtual bool IsHoldSpaceKeyToSpeakEnabled() = 0;

    /**
	 * @brief Gets the pointer to ITestAudioDeviceHelper which is used to test audio devices.
	 * @return If the function succeeds, the return value is the pointer to ITestAudioDeviceHelper. Otherwise returns nullptr.
	 */
	virtual ITestAudioDeviceHelper* GetTestAudioDeviceHelper() = 0;

    /**
	 * @brief Sets whether to enable the function of sync buttons on headset or not. 
	 * @param bEnable true indicates to enable the function, false not.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 */
	virtual SDKError EnableSyncButtonsOnHeadset(bool bEnable) = 0;

    /**
	 * @brief Checks whether the sync buttons on headset is enabled or not.
	 * @return If it is true, it means the sync buttons on headset is enabled.
	 */
	virtual bool IsSyncButtonsOnHeadsetEnabled() = 0;

    /**
	 * @brief Sets whether to enable the function of high fidelity music mode or not. 
	 * @param bEnable true indicates to enable the function, false not.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 * @note This function is valid only if mic original input is enabled, otherwise invalid.
	 */
	virtual SDKError EnableHighFidelityMusicMode(bool bEnable) = 0;

    /**
	 * @brief Checks whether the high fidelity music mode is enabled or not.
	 * @return If it is true, it means the echo cancellation is enabled
	 */
	virtual bool IsHighFidelityMusicModeDisabled() = 0;

	virtual SDKError EnableAlwaysUseSeparateRingSpk(bool bEnable) = 0;

	virtual bool IsAlwaysUseSeparateRingSpk() = 0;

	virtual bool isSupportPromptJoinAudioDialogWhenUse3rdPartyAudio() = 0;
#endif
};

/**
 * @class IRecordingSettingContextEvent
 * @brief Recording setting context callback event.
 */
class IRecordingSettingContextEvent
{
public:
	virtual ~IRecordingSettingContextEvent() {}

    /**
	 * @brief Notification of the current cloud recording storage information.
	 * @param storage_total_size Specify the total storage space.
	 * @param storage_used_size Specify the used storage space.
	 * @param allow_exceed_storage Specify whether the used space can overflow the total space. 
	 */
	virtual void onCloudRecordingStorageInfo(INT64 storage_total_size, INT64 storage_used_size, bool allow_exceed_storage) = 0;
};

/**
 * @class IRecordingSettingContext
 * @brief Recording setting interface.
 */
class IRecordingSettingContext
{
public:
    /**
	 * @brief Sets the path to save the recording file.
	 * @param szPath Specify the path to save the recording file.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 */
	virtual SDKError SetRecordingPath(const zchar_t* szPath) = 0;

    /**
	 * @brief Gets the path to save the recording file.
	 * @return The path to save the recording file.
	 */
	virtual const zchar_t* GetRecordingPath() = 0;
	
    /**
	 * @brief Sets the event of recording settings.
	 * @param pEvent The event of recording settings.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 */
	virtual SDKError SetRecordingSettingEvent(IRecordingSettingContextEvent* pEvent) = 0;
	
    /**
	 * @brief Checks if the user has the privilege to get the storage information for cloud recording.
	 * @return true indicates the user has the privilege.
	 */
	virtual bool CanGetCloudRecordingStorageInfo() = 0;

    /**
	 * @brief Gets the storage information of cloud recording.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 * @note If the return value is SDKERR_SUCCESS, IRecordingSettingContextEvent.onCloudRecordingStorageInfo() will be triggered after the information has be retrieved.
	 */
	virtual SDKError GetCloudRecordingStorageInfo() = 0;

    /**
	 * @brief Gets the recording management URL. It returns the real url only after you retrieve the callback IRecordingSettingContextEvent.onCloudRecordingStorageInfo().
	 * @return The recording management URL.
	 */
	virtual const zchar_t* GetRecordingManagementURL() = 0;
	
    /**
	 * @brief Sets if it is able to get recording management URL.
	 * @param [out]bEnable true indicates the recording management URL can be retrieved. false not. It validates only when the return value is SDKERR_SUCCESS.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 */
	virtual SDKError CanGetRecordingManagementURL(bool& bEnable) = 0;

    /**
	 * @brief Enable/Disable multi-audio stream recording.
	 * @param bEnable true indicates enabled. false not.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 */
	virtual SDKError EnableMultiAudioStreamRecord(bool bEnable) = 0;
	
    /**
	 * @brief Determines if multi-audio stream recording is enabled.
	 * @return true indicates enabled.
	 */
	virtual bool IsMultiAudioStreamRecordEnabled() = 0;
	
    /**
	 * @brief Enable/Disable watermark of timestamp.
	 * @param bEnable true indicates enabled. false not.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 */
	virtual SDKError EnableAddTimestampWatermark(bool bEnable) = 0;
	
    /**
	 * @brief Determines if the watermark of timestamps is enabled.
	 * @return true indicates enabled.
	 */
	virtual bool IsAddTimestampWatermarkEnabled() = 0;
	
    /**
	 * @brief Enable/Disable the optimization for the third party video editor.
	 * @param bEnable true indicates enabled. false not.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 */
	virtual SDKError EnableOptimizeFor3rdPartyVideoEditor(bool bEnable) = 0;
	
    /**
	 * @brief Determines if the third party video editor is enabled.
	 * @return true indicates enabled.
	 */
	virtual bool IsOptimizeFor3rdPartyVideoEditorEnabled() = 0;
	
    /**
	 * @brief Enable/Disable showing the video thumbnail when sharing.
	 * @param bEnable true indicates enabled. false not.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 */
	virtual SDKError EnableShowVideoThumbnailWhenShare(bool bEnable) = 0;
	
    /**
	 * @brief Determines if video thumbnail is enabled when sharing.
	 * @return true indicates enabled.
	 */
	virtual bool IsShowVideoThumbnailWhenShareEnabled() = 0;
	
    /**
	 * @brief Enable/Disable placing the video layout next to the shared content in recording file.
	 * @param bEnable true indicates enabled. false not.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 */
	virtual SDKError EnablePlaceVideoNextToShareInRecord(bool bEnable) = 0;
	
    /**
	 * @brief Determines if placing video next to the shared content in recording file is enabled.
	 * @return true indicates enabled.
	 */
	virtual bool IsPlaceVideoNextToShareInRecordEnabled() = 0;
#if defined(WIN32)	
    /**
	 * @brief Sets whether to enable the function of selecting the path to save the recording file after meeting.
	 * @param bEnable true indicates to enable, false not.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 */
	virtual SDKError EnableSelectRecordFileLocationAfterMeeting(bool bEnable) = 0;
	
    /**
	 * @brief Checks if the function of selecting storage path for recording file is enabled.
	 * @return true indicates enabled.
	 */
	virtual bool IsSelectRecordFileLocationAfterMeetingEnabled() = 0;
#endif
	
};

/**
 * @brief Enumeration of the network type.
 */
enum SettingsNetWorkType 
{ 
    /** Wired LAN. */
	SETTINGS_NETWORK_WIRED	= 0,
    /** WIFI. */
	SETTINGS_NETWORK_WIFI	= 1,
    /** PPP. */
	SETTINGS_NETWORK_PPP	= 2,
    /** 3G. */
	SETTINGS_NETWORK_3G		= 3,
    /** Others. */
	SETTINGS_NETWORK_OTHERS	= 4,
    /** Unknown network. */
	SETTINGS_NETWORK_UNKNOWN = -1,
};

/**
 * @brief Enumeration of the connection type.
 */
enum SettingConnectionType
{
    /** Cloud connection. */
	SETTINGS_CONNECTION_TYPE_CLOUD,
    /** Direct connection. */
	SETTINGS_CONNECTION_TYPE_DIRECT,
    /** Unknown connection. */
	SETTINGS_CONNECTION_TYPE_UNKNOWN = -1,
};

/**
 * @brief Overall statistic information.
 */
typedef struct tagOverallStatisticInfo
{
    /** Network type. */
	SettingsNetWorkType net_work_type_;
    /** Connection type. */
	SettingConnectionType connection_type_;
    /** Proxy address. */
	const zchar_t* proxy_addr_;
	tagOverallStatisticInfo()
	{
		net_work_type_ = SETTINGS_NETWORK_UNKNOWN;
		connection_type_ = SETTINGS_CONNECTION_TYPE_UNKNOWN;
		proxy_addr_ = nullptr;
	}
}OverallStatisticInfo;

/**
 * @brief The audio status information.
 */
typedef struct tagAudioSessionStatisticInfo
{
    /** Sending frequency, unit: KHz. */
	int frequency_send_;
    /** Receiving frequency, unit: KHz. */
	int frequency_recv_;
    /** Sending latency, unit: ms. */
	int latency_send_;
    /** Receiving latency, unit: ms. */
	int latency_recv_;
    /** Sending jitter, unit: ms. */
	int jitter_send_;
    /** Receiving jitter, unit: ms. */
	int jitter_recv_;
    /** Sending packet loss, unit: %. */
	float packetloss_send_;
    /** Receiving packet loss, unit: %. */
	float packetloss_recv_;

	tagAudioSessionStatisticInfo()
	{
		memset(this, 0, sizeof(tagAudioSessionStatisticInfo)); 
	}
}AudioSessionStatisticInfo;

/**
 * @brief The video status information.
 */
typedef struct tagASVSessionStatisticInfo
{
    /** Sending latency, unit: ms. */
	int latency_send_;
    /** Receiving latency, unit: ms. */
	int latency_recv_;
    /** Sending jitter, unit: ms. */
	int jitter_send_;
    /** Receiving jitter, unit: ms. */
	int jitter_recv_;
    /** Sending max packet loss, unit: %. */
	float packetloss_send_max_;
    /** Receiving max packet loss, unit: %. */
	float packetloss_recv_max_;
    /** Sending average packet loss, unit: %. */
	float packetloss_send_avg_;
    /** Receiving average packet loss, unit: %. */
	float packetloss_recv_avg_;
    /** HIWORD->height, LOWORD->width. */
	int resolution_send_;
    /** HIWORD->height, LOWORD->width. */
	int resolution_recv_;
    /** Frame per second sending. */
	int fps_send_;
    /** Frame per second receiving. */
	int fps_recv_;
	tagASVSessionStatisticInfo()
	{
		memset(this, 0, sizeof(tagASVSessionStatisticInfo));   
	}
}ASVSessionStatisticInfo;


/**
 * @class IStatisticSettingContext
 * @brief Statistic setting interface.
 */
class IStatisticSettingContext
{
public:
    /**
	 * @brief Query overall statistic information.
	 * @param info_ [out] Overall information. 
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 */
	virtual SDKError QueryOverallStatisticInfo(OverallStatisticInfo& info_) = 0;

    /**
	 * @brief Query audio statistic information.
	 * @param info_ [out] Audio information. 
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error. 
	 * @deprecated This interface is marked as deprecated, and is replaced by IMeetingService::GetMeetingAudioStatisticInfo(MeetingAudioStatisticInfo& info).
	 */
	virtual SDKError QueryAudioStatisticInfo(AudioSessionStatisticInfo& info_) = 0;

    /**
	 * @brief Query video statistic information.
	 * @param info_ [out] Video information.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 * @deprecated This interface is marked as deprecated, and is replaced by IMeetingService::GetMeetingVideoStatisticInfo(MeetingASVStatisticInfo& info).
	 */
	virtual SDKError QueryVideoStatisticInfo(ASVSessionStatisticInfo& info_) = 0;

    /**
	 * @brief Query share statistic information.
	 * @param info_ [out] Share information. 
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error. 
	 * @deprecated This interface is marked as deprecated, and is replaced by IMeetingService::GetMeetingShareStatisticInfo(MeetingASVStatisticInfo& info).
	 */
	virtual SDKError QueryShareStatisticInfo(ASVSessionStatisticInfo& info_) = 0;
};

typedef enum
{
	ZoomSDKWallpaperLayoutMode_None,
	ZoomSDKWallpaperLayoutMode_Fill,
	ZoomSDKWallpaperLayoutMode_Fit
}ZoomSDKWallpaperLayoutMode;

typedef enum
{
	ZoomSDKWallpaperSettingStatus_None,
	ZoomSDKWallpaperSettingStatus_Downloading,
	ZoomSDKWallpaperSettingStatus_Downloaded,
	ZoomSDKWallpaperSettingStatus_DownloadFail
}ZoomSDKWallpaperSettingStatus;

/**
 * @class IWallpaperItem
 * @brief Wall-paper item interface.
 */
class IWallpaperItem
{
public:
    /**
	 * @brief Gets the layout mode of the wall-paper.
	 * @return Value defined in ZoomSDKWallpaperLayoutMode enum.
	 */
	virtual ZoomSDKWallpaperLayoutMode GetWallpaperLayoutMode() = 0;

    /**
	 * @brief Gets the wall-paper ID.
	 * @return The wall-paper ID.
	 */
	virtual const zchar_t* GetWallpaperID() = 0;

    /**
	 * @brief Gets the wall-paper title.
	 * @return The wall-paper title.
	 */
	virtual const zchar_t* GetTitle() = 0;

    /**
	 * @brief Gets the wall-paper thumbnail path.
	 * @return The wall-paper thumbnail path.
	 */
	virtual const zchar_t* GetThumbnailPath() = 0;

    /**
	 * @brief Gets the full image path of the wall-paper.
	 * @return The full image path of the wall-paper.
	 */
	virtual const zchar_t* GetPath() = 0;

    /**
	 * @brief Gets the transparency of the wall-paper.
	 * @return The transparency of the wall-paper. 0 ~ 255, -1 means no transparency. Only possible in meeting wall-paper.
	 */
	virtual int GetTransparency() = 0;

    /**
	 * @brief Sets the transparency of the wall-paper.
	 * @param transparency The transparency of the wall-paper. 0 ~ 255, -1 means no transparency. Only possible in meeting wall-paper.
	 */
	virtual void SetTransparency(int transparency) = 0;

    /**
	 * @brief Sets the layout mode of the wall-paper.
	 * @param mode Value defined in ZoomSDKWallpaperLayoutMode enum.
	 */
	virtual void SetWallpaperLayoutMode(ZoomSDKWallpaperLayoutMode mode) = 0;

	virtual ~IWallpaperItem() {};
};

/**
 * @class IWallpaperSettingContextEvent
 * @brief Meeting wall-paper context Callback Event.
 */
class IWallpaperSettingContextEvent
{
public:
    /**
	 * @brief Callback event of notification that the meeting wall-paper item is changed.
	 * @param item The config changed.
	 */
	virtual void onMeetingWallpaperChanged(IWallpaperItem* item) = 0;

    /**
	 * @brief Callback event of notification that download status of the meeting wall-paper is changed.
	 * @param status The download status of the meeting wall-peper.
	 * @param wallpaperId The download meeting wall-paper image ID.
	 */
	virtual void onMeetingWallpaperImageDownloadStatus(ZoomSDKWallpaperSettingStatus status, const zchar_t* wallpaperId) = 0;

#if defined(WIN32)
    /**
	 * @brief Callback event of notification that the personal wall-paper item is changed.
	 * @param item The config changed.
	 */
	virtual void onPersonalWallpaperChanged(IWallpaperItem* item) = 0;

    /**
	 * @brief Callback event of notification that download status of the personal wall-paper is changed.
	 * @param status The download status of the personal wall-peper.
	 * @param wallpaperId The download personal wall-paper image ID.
	 */
	virtual void onPersonalWallpaperImageDownloadStatus(ZoomSDKWallpaperSettingStatus status, const zchar_t* wallpaperId) = 0;
#endif
};

/**
 * @class IWallpaperSettingContext
 * @brief Meeting Wall-paper setting interface.
 */
class IWallpaperSettingContext
{
public:
    /**
	 * @brief Meeting wall-paper callback handler. 
	 * @param pEvent A pointer to the IWallpaperSettingContextEvent that receives wall-paper event. 
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 * @note Call the function before using any other interface of the same class.
	 */
	virtual SDKError SetEvent(IWallpaperSettingContextEvent* pEvent) = 0;

    /**
	 * @brief Determines if the meeting wall-paper feature enabled by OP.
	 * @return true indicates the feature enabled. Otherwise false.
	 */
	virtual bool IsMeetingWallpaperEnabled() = 0;

    /**
	 * @brief Determines if meeting wall-paper thumbnail ready.
	 * @return true indicates ready. Otherwise false.
	 */
	virtual bool IsMeetingWallpaperThumbsReady() = 0;

    /**
	 * @brief Gets the meeting wall-paper item.
	 * @return The current using meeting wall-paper config.
	 * @note If select None, the wall-paper ID is empty.
	 */
	virtual IWallpaperItem* GetCurrentMeetingWallpaperItem() = 0;

    /**
	 * @brief Gets the meeting wall-paper list.
	 * @return The meeting wall-paper list.
	 */
	virtual IList<IWallpaperItem* >* GetMeetingWallpaperList() = 0;

    /**
	 * @brief Sets the meeting wall-paper item.
	 * @param item The meeting wall-paper item need to set.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 */
	virtual SDKError SetMeetingWallpaper(IWallpaperItem* item) = 0;

    /**
	 * @brief Gets the meeting wall-paper item by wall-paper ID.
	 * @return The meeting wall-paper with the wall-paper ID.
	 */
	virtual IWallpaperItem* GetMeetingWallpaperItemByID(const zchar_t* wallpaperID) = 0;

#if defined(WIN32)	
    /**
	 * @brief Determines if the personal wall-paper feature enabled by OP.
	 * @return true indicates the feature enabled. Otherwise false.
	 */
	virtual bool IsPersonalWallpaperEnabled() = 0;

    /**
	 * @brief Gets the current user's persional wall-paper item.
	 * @return The current user's personal wall-paper config.
	 * @note Only login user has this config. If select None, the wall-paper ID is empty.
	 */
	virtual IWallpaperItem* GetCurrentPersonalWallpaperItem() = 0;

    /**
	@brief Gets the current user's personal wall-paper list.
	 * @return The current user's personal wall-paper list.
	 * @note Only login user has this config list.
	 */
	virtual IList<IWallpaperItem* >* GetPersonalWallpaperList() = 0;

    /**
	 * @brief Sets the current user's personal wall-paper.
	 * @param item Personal wall-paper item need to set.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 * @note Only valid for login user.
	 */
	virtual SDKError SetPersonalWallpaper(IWallpaperItem* item) = 0;

    /**
	 * @brief Gets the personal wall-paper item by wall-paper ID.
	 * @return The personal wall-paper item with the wall-paper ID.
	 * @note Only valid for login user.
	 */
	virtual IWallpaperItem* GetPersonalWallpaperItemByID(const zchar_t* wallpaperID) = 0;
#endif
};

#if defined(WIN32)	
/**
 * @class IAccessibilitySettingContext
 * @brief Accessibility setting interface.
 */
class IAccessibilitySettingContext
{
public:
    /**
	 * @brief Enable/Disable Always Show Meeting Controls in meeting window.
	 * @param bEnable true indicates enabled.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 */
	virtual SDKError EnableAlwaysShowMeetingControls(bool bEnable) = 0;

    /**
	 * @brief get the current setting status of Always Show Meeting Controls in meeting window.
	 * @param bEnable true indicates enabled.
	 * @return If the return value is SDKERR_SUCCESS means that always show meeting controls is enabled.
	 */
	virtual SDKError IsAlwaysShowMeetingControlsEnable(bool& bEnable) = 0;
};

/**
 * @class ISettingUIStrategy
 * @brief Setting user strategy interface.
 */
class ISettingUIStrategy
{
public:
    /**
	 * @brief Hide the link to check the advanced settings on the General Setting page or not.
	 * @param bDisable true indicates to hide the link.
	 */
	virtual void DisableAdvancedFeatures4GeneralSetting(bool bDisable) = 0;

    /**
	 * @brief Hide the Account Setting page or not.
	 * @param bDisable true indicates to hide the account setting page.
	 * @deprecated This interface is marked as deprecated. Use \link ISettingUIStrategy::ConfSettingDialogShownTabPage \endlink instead.
	 */
	virtual void DisableAccountSettingTabPage(bool bDisable) = 0;

    /**
	 * @brief Custom the tab page show or hide
	 * @param showOption true indicates to show the corresponding tab page for each item.
	 */
	virtual void ConfSettingDialogShownTabPage(SettingDlgShowTabPageOption showOption) = 0;

    /**
	 * @brief Sets the visibility of the AUTOMATICALLY COPY INVITE URL check-box on the General Setting page.
	 * @param bHide true indicates to hide the check box.
	 */
	virtual void HideAutoCopyInviteLinkCheckBox(bool bHide) = 0;

    /**
	 * @brief Custom the url link show or hide
	 * @param showOption true indicates to show the corresponding url link for each item.
	 */
	virtual void ConfigToShowUrlLinksInSetting(SettingDlgShowUrlOption showOption) = 0;
};
/**
 * @class IVirtualBGImageInfo
 * @brief Virtual background image information interface.
 */
class IVirtualBGImageInfo
{
public:
    /**
	 * @brief Determines the usage of current image.
	 * @return true indicates that current image is used as the virtual background image.
	 */
	virtual bool isSelected() = 0;

    /**
	 * @brief Determines the current image can be deleted from the list.
	 * @return true indicates that current image can be deleted from the list.
	 */
	virtual bool isAllowDelete() = 0;
    /**
	 * @brief Gets the file path of current image.
	 * @return If the function succeeds, the return value is the file path of current image. Otherwise, this function fails and returns nullptr.
	 */
	virtual const zchar_t* GetImageFilePath() = 0;

    /**
	 * @brief Gets the name of current image.
	 * @return If the function succeeds, the return value is the name of current image. Otherwise, this function fails and returns nullptr.
	 */
	virtual const zchar_t* GetImageName() = 0;

	virtual ~IVirtualBGImageInfo() {};
};

enum VBVideoError {
	VB_VideoError_None = 0,
	VB_VideoError_UnknownFormat,
	VB_VideoError_ResolutionHigh1080P,
	VB_VideoError_ResolutionHigh720P,
	VB_VideoError_ResolutionLow,
	VB_VideoError_PlayError,
	VB_VideoError_OpenError,
};

/**
 * @class IVirtualBGSettingContextEvent
 * @brief Virtual background context Callback Event.
 */
class IVirtualBGSettingContextEvent
{
public:
	virtual ~IVirtualBGSettingContextEvent() {}

    /**
	 * @brief Notification of the default virtual background images supplied by ZOOM are downloaded.
	 */
	virtual void onVBImageDidDownloaded() = 0;
	
    /**
	 * @brief Notification of the virtual background effect is updated with the selected color.
	 * @param selectedColor The RGB value of the selected color, organized in the format 0xFFRRGGBB. 
	 */
	virtual void onGreenVBDidUpdateWithReplaceColor(DWORD selectedColor) = 0;

    /**
	 * @brief Notification of the virtual background image is changed.
	 */
	virtual void onSelectedVBImageChanged() = 0;

    /**
	 * @brief Notification of creating the thumb of a virtual background video is success.
	 * @param file_path The file name with full path which you can use to generate your thumb for the virtual background video.
	 */
	virtual void OnVideoThumbReady(const zchar_t* file_path) = 0;

    /**
	 * @brief Notification of creating the thumb of a virtual background video is failed.
	 * @param file_path The file name with full path which sdk generates from the virtual background video.
	 * @param error The fail reason.
	 */
	virtual void OnVideoThumbError(const zchar_t* file_path, VBVideoError error) = 0;

    /**
	 * @brief Notification of  playing a virtual background video is failed.
	 * @param file_path The file name with full path which sdk generates from the virtual background video.
	 * @param error The fail reason.
	 */
	virtual void OnVideoPlayError(const zchar_t* file_path, VBVideoError error) = 0;
};

/**
 * @class IVirtualBGSettingContext
 * @brief Virtual background setting interface.
 */
class IVirtualBGSettingContext
{
public:
    /**
	 * @brief Virtual background callback handler. 
	 * @param pEvent A pointer to the IVirtualBGSettingContextEvent that receives virtual background event. 
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 * @note Call the function before using any other interface of the same class.
	 */
	virtual SDKError SetVirtualBGEvent(IVirtualBGSettingContextEvent* pEvent) = 0;

    /**
	 * @brief Determines if the virtual background feature is supported by the meeting.
	 * @return true indicates that the meeting supports the virtual background feature.
	 */
	virtual bool IsSupportVirtualBG() = 0;

    /**
	 * @brief Determines if the smart virtual background feature can be supported by the machine.
	 * @return true indicates that the machine can supports to use smart virtual background feature.
	 */
	virtual bool IsDeviceSupportSmartVirtualBG() = 0;

    /**
	 * @brief Determines if the video virtual background feature is supported by the meeting.
	 * @return true indicates that the meeting supports the video virtual background feature.
	 */
	virtual bool IsSupportVirtualBackgroundVideo() = 0;

    /**
	 * @brief Determines if the smart virtual background video feature can be supported by the machine. 
	 * @note For more information, please visit <https://support.zoom.com/hc/en/article?id=zm_kb&sysparm_article=KB0060007>
	 * @return true indicates that the machine can supports to use smart virtual background video feature.
	 */
	virtual bool IsDeviceSupportSmartVirtualBackgroundVideo() = 0;

    /**
	 * @brief Determines if the green virtual background video feature can be supported by the machine. 
	 * @note For more information, please visit <https://support.zoom.com/hc/en/article?id=zm_kb&sysparm_article=KB0060007>
	 * @return true indicates that the machine can supports to use green virtual background video feature.
	 */
	virtual bool IsDeviceSupportGreenVirtualBackgroundVideo() = 0;

    /**
	 * @brief Determines if the green screen is using for the virtual background feature in the meeting.
	 * @return true indicates to use the green screen for the virtual background feature.
	 */
	virtual bool IsUsingGreenScreenOn() = 0;

    /**
	 * @brief Sets to use the green screen for the virtual background feature.
	 * @param bUse Specify to use the green screen or not.true indicates using the green screen. false means using smart virtual background feature.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 * @note If the machine can not support smart virtual background feature, Calling of this interface with parameter 'false'will return SDKERR_WRONG_USAGE.
	 */
	virtual SDKError SetUsingGreenScreen(bool bUse) = 0;

    /**
	 * @brief Determines if the adding new virtual background item feature is supported by the meeting
	 * @return true indicates that the meeting supports adding new virtual background item feature.
	 */
	virtual bool IsAllowToAddNewVBItem() = 0;

    /**
	 * @brief Determines if the removing virtual background item feature is supported by the meeting
	 * @return true indicates that the meeting supports removing virtual background item feature.
	 *@deprecated This interface is marked as deprecated.
	 */
	virtual bool isAllowToRemoveVBItem() = 0;

    /**
	 * @brief Add a new image as the virtual background image and to the image list.
	 * @param file_path Specify the file name of the image. It must be the full path with the file name.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 */
	virtual SDKError AddBGImage(const zchar_t* file_path) = 0;

    /**
	 * @brief Remove an image from the virtual background image list.
	 * @param pRemoveImage Specify the image to remove.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 */
	virtual SDKError RemoveBGImage(IVirtualBGImageInfo* pRemoveImage) = 0;

    /**
	 * @brief Gets the list of the virtual background images.
	 * @return If there are images in the list, the return value is a list of the poiters to IVirtualBGImageInfo. Otherwise return nullptr.
	 */
	virtual IList<IVirtualBGImageInfo* >* GetBGImageList() = 0;

    /**
	 * @brief Specify an image to be the virtual background image.
	 * @param pImage Specify the image to use.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 */
	virtual SDKError UseBGImage(IVirtualBGImageInfo* pImage) = 0;

    /**
	 * @brief Gets the selected color after called BeginSelectReplaceVBColor() and selected a color.
	 * @return If the function succeeds, the return value is the selected color. Otherwise 0xFF000000. The value is the same one as the callback IVirtualBGSettingContextEvent.onGreenVBDidUpdateWithReplaceColor() does.
	 */
	virtual DWORD GetBGReplaceColor() = 0;

    /**
	 * @brief Starts to capture a color from video preview.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 */
	virtual SDKError BeginSelectReplaceVBColor() = 0;

    /**
	 * @brief Add a new video as the virtual background video and to the video list.
	 * @param file_path Specify the file name of the video. It must be the full path with the file name.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 */
	virtual SDKError AddBGVideo(const zchar_t* file_path) = 0;

    /**
	 * @brief Remove a video from the virtual background video list.
	 * @param pRemoveVideo Specify the video to remove.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 */
	virtual SDKError RemoveBGVideo(IVirtualBGImageInfo* pRemoveVideo) = 0;

    /**
	 * @brief Gets the list of the virtual background videoes.
	 * @return If there are videoes in the list, the return value is a list of the poiters to IVirtualBGImageInfo. Otherwise returns nullptr.
	 */
	virtual IList<IVirtualBGImageInfo* >* GetBGVideoList() = 0;

    /**
	 * @brief Specify a video to be the virtual background video.
	 * @param pVideo Specify the video to use.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 */
	virtual SDKError UseBGVideo(IVirtualBGImageInfo* pImage) = 0;

    /**
	 * @brief Gets the pointer to ITestVideoDeviceHelper which is used to preview the video with virtual background image.
	 * @return If the function succeeds, the return value is the pointer to ITestVideoDeviceHelper. Otherwise returns nullptr.
	 */
	virtual ITestVideoDeviceHelper* GetTestVideoDeviceHelper() = 0;
};

typedef enum
{
	ZoomSDKVideoEffectType_None = 0,
	ZoomSDKVideoEffectType_Filter = 1,
	ZoomSDKVideoEffectType_Frame = 2,
	ZoomSDKVideoEffectType_CustomFilter = 3,
	ZoomSDKVideoEffectType_Sticker = 4,
}ZoomSDKVideoEffectType;

/**
 * @class IVideoFilterImageInfo
 * @brief Video filter image information interface.
 */
class IVideoFilterImageInfo
{
public:
    /**
	 * @brief Determines the usage of current image.
	 * @return tue means that current image is used as the video filter image.
	 */
	virtual bool isSelected() = 0;

    /**
	 * @brief Gets the file path of current image.
	 * @return If the function succeeds, the return value is the file path of current image. Otherwise returns nullptr.
	 */
	virtual const zchar_t* GetImageFilePath() = 0;

    /**
	 * @brief Gets the name of current image.
	 * @return If the function succeeds, the return value is the name of current image. Otherwise returns nullptr.
	 */
	virtual const zchar_t* GetImageName() = 0;

    /**
	 * @brief Gets the type of current image.
	 * @return If the function succeeds, the return value is the type of current image. 
	 * @note If select none as video filter, the type value will be ZoomSDKVideoEffectType_None.
	 */
	virtual ZoomSDKVideoEffectType GetType() = 0;

    /**
	 * @brief Gets the index of current image.
	 * @return If the function succeeds, it returns the index of current image. Otherwise, this function fails and returns nullptr.
	 * @note If select none as video filter, the index value will be -1.
	 */
	virtual int GetIndex() = 0;

	virtual ~IVideoFilterImageInfo() {};
};

/**
 * @class IVideoFilterSettingContextEvent
 * @brief Video filter context Callback Event.
 */
class IVideoFilterSettingContextEvent
{
public:
    /**
	 * @brief Notification of the thumbnails of all video filter items have been downloaded.
	 */
	virtual void onVideoFilterItemThumnailsDownloaded() = 0;

    /**
	 * @brief Notification of the selected video filter item is downloading.
	 * @param type The type of the selected video filter item.
	 * @param index The index of the selected video filter item. 
	 */
	virtual void onVideoFilterItemDataDownloading(ZoomSDKVideoEffectType type, int index) = 0;

    /**
	 * @brief Notification of the selected video filter item whether has been downloaded successfully.
	 * @param type The type of the selected video filter item.
	 * @param index The index of the selected video filter item. 
	 * @param bSuccess true indicates the selected video filter item has been downloaded successfully.
	 */
	virtual void onVideoFilterItemDataDownloaded(bool bSuccess, ZoomSDKVideoEffectType type, int index) = 0;
};

/**
 * @class IVideoFilterSettingContext
 * @brief Video filter setting interface.
 */
class IVideoFilterSettingContext
{
public:
    /**
	 * @brief Video filter callback handler. 
	 * @param pEvent A pointer to the IVideoFilterSettingContextEvent that receives video filter event. 
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 * @note Call the function before using any other interface of the same class.
	 */
	virtual SDKError SetVideoFilterEvent(IVideoFilterSettingContextEvent* pEvent) = 0;


    /**
	 * @brief Determines if the video filter feature is supported by the meeting.
	 * @return true indicates that the meeting supports the video filter feature.
	 */
	virtual bool IsSupportVideoFilter() = 0;

    /**
	 * @brief Determines if the video filter feature is enabled.
	 * @return true indicates the video filter feature is enabled.
	 */
	virtual bool IsVideoFilterEnabled() = 0;

    /**
	 * @brief Determines if the video filter feature is locked.
	 * @return true indicates the video filter feature is locked.
	 */
	virtual bool IsVideoFilterLocked() = 0;

    /**
	 * @brief Gets the list of the video filter images.
	 * @return If there are images in the list, the return value is a list of the poiters to IVideoFilterImageInfo. Otherwise returns nullptr.
	 */
	virtual IList<IVideoFilterImageInfo* >* GetVideoFilterImageList() = 0;

    /**
	 * @brief Specify an image to be the video filter image.
	 * @param pImage Specify the image to use.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 */
	virtual SDKError UseVideoFilterImage(IVideoFilterImageInfo* pImage) = 0;

    /**
	 * @brief Gets the pointer to ITestVideoDeviceHelper which is used to preview the video with virtual background image.
	 * @return If the function succeeds, the return value is the pointer to ITestVideoDeviceHelper. Otherwise returns nullptr.
	 */
	virtual ITestVideoDeviceHelper* GetTestVideoDeviceHelper() = 0;
};

/**
 * @class I3DAvatarSettingContextEvent
 * @brief 3D avatar context callback event.
 */
class I3DAvatarSettingContextEvent
{
public:
	virtual ~I3DAvatarSettingContextEvent() {}

    /**
	 * @brief Notification of  all 3D avatar items' thumbnails have been downloaded
	 */
	virtual void on3DAvatarItemThumbnailsDownloaded() = 0;

    /**
	 * @brief Notification of the selected 3d avatar item is downloading.
	 * @param index The index of the selected 3d avatar item. 
	 */
	virtual void on3DAvatarItemDataDownloading(int index) = 0;

    /**
	 * @brief Notification of whether or not the selected 3d avatar item has been downloaded successfully.
	 * @param index The index of the selected 3d avatar item. 
	 * @param bSuccess true indicates the selected 3d avatar item has been downloaded successfully.
	 */
	virtual void on3DAvatarItemDataDownloaded(bool bSuccess, int index) = 0;
};

/**
 * @class I3DAvatarSettingContext
 * @brief 3D avatar setting interface.
 */
class I3DAvatarSettingContext
{
public:
    /**
	 * @brief 3D avatar callback handler. 
	 * @param pEvent A pointer to the I3DAvatarSettingContextEvent that receives 3D avatar event. 
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 * @note Call the function before using any other interface of the same class.
	 */
	virtual SDKError SetEvent(I3DAvatarSettingContextEvent* pEvent) = 0;

    /**
	 * @brief Determines if the 3D avatar feature is supported by video device.
	 * @return true indicates that the video device supports the 3D avatar feature.
	 */
	virtual bool Is3DAvatarSupportedByDevice() = 0;

    /**
	 * @brief Determines if the 3D avatar feature is enabled.
	 * @return true indicates the video filter feature is enabled.
	 */
	virtual bool Is3DAvatarEnabled() = 0;

    /**
	 * @brief Enable/Disable the selected 3D avatar effect always used by the future meeting.
	 * @param bEnable true indicates enabled. false not.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 */
	virtual SDKError Enable3DAvatarEffectForAllMeeting(bool bEnable) = 0;

    /**
	 * @brief Determines if the selected 3D avatar effect is always used by the future meetings.
	 * @return true indicates that the selected 3D avatar effect still applies to future meetings.
	 */
	virtual bool Is3DAvatarEffectForAllMeetingEnabled() = 0;

    /**
	 * @brief Gets the list of the 3D avatar images.
	 * @return If there are images in the list, the return value is a list of the pointers to I3DAvatarImageInfo. Otherwise returns nullptr.
	 */
	virtual IList<I3DAvatarImageInfo* >* Get3DAvatarImageList() = 0;

    /**
	 * @brief Specify an image to be the the 3D avatar image.
	 * @param pImage Specify the image to use.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 */
	virtual SDKError Set3DAvatarImage(I3DAvatarImageInfo* pImage) = 0;

    /**
	 * @brief Gets the pointer to ITestVideoDeviceHelper which is used to preview the video with 3D avatar image.
	 * @return If the function succeeds, it returns the pointer to ITestVideoDeviceHelper. Otherwise, this function fails and returns nullptr.
	 */
	virtual ITestVideoDeviceHelper* GetTestVideoDeviceHelper() = 0;

    /**
	 * @brief Gets the pointer to ILipSyncAvatarPreviewHelper which is used to preview the lip-sync avatar.
	 * @return If the function succeeds, the return value is the pointer to ILipSyncAvatarPreviewHelper. Otherwise returns nullptr.
 
	 */
	virtual ILipSyncAvatarPreviewHelper* GetLipSyncAvatarPreviewHelper() = 0;
};

typedef enum
{
	ZoomSDKFaceMakeupType_Mustache,
	ZoomSDKFaceMakeupType_Eyebrow,
	ZoomSDKFaceMakeupType_Lip
}ZoomSDKFaceMakeupType;

/**
 * @class IFaceMakeupImageInfo
 * @brief face makeup image information interface.
 */
class IFaceMakeupImageInfo
{
public:
    /**
	 * @brief Gets the type of current image.
	 * @return If the function succeeds, the return value is the type of current image. 
	 */
	virtual ZoomSDKFaceMakeupType GetFaceMakeupType() = 0;

    /**
	 * @brief Determines if the current item is being used.
	 * @return true indicates that the current image is used as the face makeup image.
	 */
	virtual bool IsSelected() = 0;

    /**
	 * @brief Gets the file path of the current image.
	 * @return If the function succeeds, the return value is the file path of current image. Otherwise returns nullptr.
	 */
	virtual const zchar_t* GetImageFilePath() = 0;

    /**
	 * @brief Gets the name of the current image.
	 * @return If the function succeeds, the return value is the name of the current image. Otherwise returns nullptr.
	 */
	virtual const zchar_t* GetImageName() = 0;

    /**
	 * @brief Gets the index of the current face makeup.
	 * @return If the function succeeds, it returns the index of the current image.
	 * @note If no image is selected, the index value is -1.
	 */
	virtual int GetIndex() = 0;

	virtual ~IFaceMakeupImageInfo() {};
};

/**
 * @class IFaceMakeupSettingContextEvent
 * @brief Face makeup context callback event.
 */
class IFaceMakeupSettingContextEvent
{
public:
	virtual ~IFaceMakeupSettingContextEvent() {}

    /**
	 * @brief Notification of all face makeup items' thumbnails were downloaded
	 */
	virtual void onFaceMakeupItemThumbnailsDownloaded(ZoomSDKFaceMakeupType type) = 0;

    /**
	 * @brief Notification of the selected face makeup item is downloading.
	 * @param index The index of the selected face makeup item. 
	 */
	virtual void onFaceMakeupItemDataDownloading(ZoomSDKFaceMakeupType type, int index) = 0;

    /**
	 * @brief Notification of whether or not the selected face makeup item has downloaded successfully.
	 * @param index The index of the selected face makeup item. 
	 * @param bSuccess true indicates the selected face makeup item has downloaded successfully.
	 */
	virtual void onFaceMakeupItemDataDownloaded(bool bSuccess, ZoomSDKFaceMakeupType type, int index) = 0;
};

/**
 * @class IFaceMakeupSettingContext
 * @brief face makeup setting interface.
 */
class IFaceMakeupSettingContext
{
public:
    /**
	 * @brief face makeup callback handler. 
	 * @param pEvent A pointer to the IFaceMakeupSettingContextEvent that receives face makeup event. 
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  	
	 * @note Call the function before using any other interface of the same class.
	 */
	virtual SDKError SetEvent(IFaceMakeupSettingContextEvent* pEvent) = 0;

    /**
	 * @brief Determines if the face makeup feature is enabled.
	 * @return true indicates that the face makeup feature is enabled.
	 */
	virtual bool IsFaceMakeupEnabled() = 0;

    /**
	 * @brief Determines if the meeting supports the the face makeup feature. 
	 * @return true indicates that the meeting supports the face makeup feature.
	 */
	virtual bool IsSupportFaceMakeup() = 0;

    /**
	 * @brief Enable/Disable the selected face makeup effect always used by the future meeting.
	 * @param bEnable true indicates enabled. false not.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 */
	virtual SDKError EnableFaceMakeupEffectForAllMeeting(bool bEnable) = 0;

    /**
	 * @brief Determines if the selected face makeup effect is always used by future meetings.
	 * @return true indicates that the selected face makeup effect still applies to future meetings.
	 */
	virtual bool IsFaceMakeupEffectForAllMeetingEnabled() = 0;

    /**
	 * @brief Gets the list of the face makeup images.
	 * @return If there are images in the list, the return value is a list of the pointers to IFaceMakeupImageInfo. Otherwise returns nullptr.
	 */
	virtual IList<IFaceMakeupImageInfo* >* GetFaceMakeupImageList() = 0;

    /**
	 * @brief Specify an image to be face makeup image.
	 * @param pImage Specify the image to use.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError SetFaceMakeupImage(IFaceMakeupImageInfo* pImage) = 0;

    /**
	 * @brief Enable/Disable the lip face makeup effect.
	 * @param bEnable true indicates enabled. false not.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 */
	virtual SDKError SetLipsFaceMakeup(bool bEnable) = 0;

    /**
	 * @brief Sets the colors of face makeup effect.
	 * @param type The specified color is used on which face makeup type.
	 * @param color Specify the color of the face makeup effect in RGB format.  
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 */
	virtual SDKError SetColor(ZoomSDKFaceMakeupType type, unsigned long color) = 0;

    /**
	 * @brief Sets the transparency of face makeup effect.
	 * @param type The specified transparency is used on which face makeup type.
	 * @param opactity Specify the transparency of the face makeup effect. The value should between 0 to 100.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 */
	virtual SDKError SetOpactity(ZoomSDKFaceMakeupType type, unsigned int opactity) = 0;

    /**
	 * @brief Disables all the face makeup effect and reset color/opactity value to default value.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 */
	virtual SDKError ResetAllFaceMakeupEffect() = 0;

    /**
	 * @brief Gets the pointer to ITestVideoDeviceHelper which is used to preview the video with face makeup image.
	 * @return If the function succeeds, the return value is the pointer to ITestVideoDeviceHelper. Otherwise returns nullptr.
	 */
	virtual ITestVideoDeviceHelper* GetTestVideoDeviceHelper() = 0;
};
#endif 

/**
 * @class IShareSettingContext
 * @brief Share setting interface.
 */
class IShareSettingContext
{
public:

    /**
	 * @brief Enables or disable to auto-fit the ZOOM window when viewing the shared content.
	 * @param bEnable true indicates to resize automatically.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 */
	virtual SDKError EnableAutoFitToWindowWhenViewSharing(bool bEnable) = 0;

    /**
	 * @brief Determines if it is able to auto-fit the ZOOM window when viewing the shared content.
	 * @return true indicates to resize automatically.
	 */
	virtual bool IsAutoFitToWindowWhenViewSharingEnabled() = 0;

    /**
	 * @brief Enables or disable TCP connecting when sharing.
	 * @param bEnable true indicates to use TCP connecting when sharing.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 */
	virtual SDKError EnableTCPConnectionWhenSharing(bool bEnable) = 0;

    /**
	 * @brief Determines if it is enable use TCP connection when sharing.
	 * @return true indicates to enter the full screen video mode.
	 */
	virtual bool IsTCPConnectionWhenSharing() = 0;

    /**
	 * @brief Determines if the operating system supports the GPU acceleration when user shares.
	 * @return true indicates support.
	 */
	virtual bool IsCurrentOSSupportAccelerateGPUWhenShare() = 0;

    /**
	 * @brief Enable/Disable the GPU acceleration when user shares.
	 * @param bEnable true indicates to enable the acceleration. false not.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 */
	virtual SDKError EnableAccelerateGPUWhenShare(bool bEnable) = 0;

    /**
	 * @brief Determines if GPU acceleration is enabled when user shares.
	 * @param [out]bEnable true indicates the GPU acceleration is enabled. false not. It validates only when the return value is SDKERR_SUCCESS. 
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 */
	virtual SDKError IsAccelerateGPUWhenShareEnabled(bool& bEnable) = 0;

    /**
	 * @brief Sets the visibility of the green border when sharing the application.
	 * @param bShow true indicates to display the frame. false hide.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 */
	virtual SDKError EnableGreenBorderWhenShare(bool bEnable) = 0;

    /**
	 * @brief Determines if the green border is enabled when user shares.
	 * @return true if support. Otherwise, false.
	 */
	virtual bool IsGreenBorderEnabledWhenShare() = 0;

    /**
	 * @brief Determines if the 'limited sharing fps' feature is enabled when user shares.
	 * @return true indicates support.
	 */
	virtual bool IsLimitFPSEnabledWhenShare() = 0;

    /**
	 * @brief Enable/disable the 'limited sharing fps' feature when uses shares.
	 * @param bEnable true indicates to enable the litmited fps feature. false hide.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 */
	virtual SDKError EnableLimitFPSWhenShare(bool bEnable) = 0;

    /**
	 * @brief Gets the limited sharing fps value when the 'limited sharing fps' feature is enabled.
	 */
	virtual LimitFPSValue GetLimitFPSValueWhenShare() = 0;

    /**
	 * @brief Sets the limited sharing fps value when the 'limited sharing fps' feature is enabled.
	 * @param value Specifies the limited fps value. It validates only when the 'limited sharing fps' feature is enabled.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 */
	virtual SDKError SetLimitFPSValueWhenShare(LimitFPSValue value) = 0;

    /**
	 * @brief Enable/Disable to show the userself's app window when shares.
	 * @param bEnable true indicates to enable to show the window. false not.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 */
	virtual SDKError EnableShowMyAppWindowWhenShare(bool bEnable) = 0;

    /**
	 * @brief Determines if it is enable to show the userself's app window when shares.
	 * @return true indicates to show the userself's app window when shares.
	 */
	virtual bool IsShowMyAppWindowWhenShareEnabled() = 0;

    /**
	 * @brief Determines if the feature that showing the userself's app window when shares is supported.
	 * @return true indicates to support.
	 */
	virtual bool IsSupportShowMyAppWindowWhenShare() = 0;


    /**
	 * @brief Determines if it is silence system notification when sharing on.
	 * @return true indicates to silence system notification when sharing on.
	 */
	virtual bool IsDoNotDisturbInSharingOn() = 0;

    /**
	 * @brief Enable/Disable to silence system notification when sharing on.
	 * @param bEnable true indicates to silence system notification when sharing on. false not.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 */
	virtual SDKError EnableDoNotDisturbInSharing(bool bEnable) = 0;

    /**
	 * @brief Enable/Disable the GPU acceleration when a user adds annotations on a shared screen or whiteboard.
	 * @param bEnable true indicates to enable acceleration.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 */
	virtual SDKError EnableHardwareAcceleratedAnnotation(bool bEnable) = 0;

    /**
	 * @brief Determines if GPU acceleration is enabled when user use annotations on a shared screen or whiteboard.
	 * @param [out]bEnable true indicates the GPU acceleration is enabled. It validates only when the return value is SDKERR_SUCCESS. 
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 */
	virtual SDKError IsAnnotationHardwareAccelerated(bool& bEnable) = 0;

    /**
	 * @brief Enable/Disable the GPU acceleration when user shares video.
	 * @param bEnable true indicates to enable the acceleration.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 */
	virtual SDKError EnableHardwareAcceleratedVideoSharing(bool bEnable) = 0;

    /**
	 * @brief Determines if GPU acceleration is enabled when a user shares video.
	 * @param [out]bEnable true indicates the GPU acceleration is enabled. false indicates the GPU acceleration is not. It validates only when the return value is SDKERR_SUCCESS. 
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 */
	virtual SDKError IsVideoSharingHardwareAccelerated(bool& bEnable) = 0;

	/**
	 * @brief Enable or disable automatic video dimming when sharing flashing content. When enabled, the video is automatically dimmed when the shared content contains flashing elements.
	 * @param enable true to enable automatic dimming, false to disable.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 */
	virtual SDKError EnableShareContentFlashDetection(bool bEnable) = 0;
	/**
	 * @brief Query whether automatic dimming of video when sharing content flashes is enabled.
	 * @return true if the feature is enabled, false otherwise.
	 */
	virtual bool IsShareContentFlashDetectionEnabled() = 0;
#if defined(WIN32)
    /**
	 * @brief Sets the window size type when viewing the sharing.
	 * @param eType Specifies the window size type. 
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 */
	virtual SDKError SetWindowSizeTypeWhenViewShare(WindowSizeType eType) = 0;

    /**
	 * @brief Gets the window size type when view share.
	 */
	virtual WindowSizeType GetWindowSizeTypeWhenViewShare() = 0;

    /**
	 * @brief Enable/disable remote control of all applications.
	 * @param bEnable true indicates to enable the remote control. false not.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 */
	virtual SDKError EnableRemoteControlAllApplications(bool bEnable) = 0;

    /**
	 * @brief Determines if remote control of all applications is enabled.
	 * @return true indicates enabled.
	 */
	virtual bool IsRemoteControlAllApplicationsEnabled() = 0;

    /**
	 * @brief Gets the share option in meeting.
	 * @param [out] shareOption Specifies the share option in meeting.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 */
	virtual SDKError GetShareOptionWhenShareInMeeting(ShareOptionInMeeting& shareOption) = 0;

    /**
	 * @brief Sets the share option in meeting.
	 * @param shareOption Specifies the share option in meeting.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 */
	virtual SDKError SetShareOptionWhenShareInMeeting(ShareOptionInMeeting shareOption) = 0;

    /**
	 * @brief Gets the share select mode.
	 * @param [out] select_mode Specifies the share select mode.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 */
	virtual SDKError GetShareOptionWhenShareApplication(ShareSelectMode& select_mode) = 0;

    /**
	 * @brief Sets the share select mode.
	 * @param select_mode Specifies the share select mode.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 */
	virtual SDKError SetShareOptionWhenShareApplication(ShareSelectMode select_mode) = 0;

    /**
	 * @brief Gets the share option to room.
	 * @param [out] share_option Specifies the share option to room.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 */
	virtual SDKError GetShareOptionWhenShareInDirectShare(ShareOptionToRoom& share_option) = 0;

    /**
	 * @brief Gets the share option to room.
	 * @param share_option Specifies the share option to room.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 */
	virtual SDKError SetShareOptionWhenShareInDirectShare(ShareOptionToRoom share_option) = 0;

    /**
	 * @brief set the screen capture mode.
	 * @param capture_mode Specifies the screen capture mode.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 */
	virtual SDKError SetScreenCaptureMode(ScreenCaptureMode capture_mode) = 0;

    /**
	 * @brief Gets the screen capture mode.
	 * @param [out] capture_mode Specifies the screen capture mode.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 */
	virtual SDKError GetScreenCaptureMode(ScreenCaptureMode& capture_mode) = 0;
#endif
};

/**
 * @class ISettingService
 * @brief Meeting setting interface.
 */
class ISettingService
{
public:

    /**
	 * @brief Gets general setting interface.
	 * @return If the function succeeds, the return value is an object pointer to IGeneralSettingContext. Otherwise returns nullptr.
 	 */
	virtual IGeneralSettingContext* GetGeneralSettings() = 0;

    /**
	 * @brief Gets audio setting interface.
	 * @return If the function succeeds, the return value an object pointer to IAudioSettingContext. Otherwise returns nullptr.
 	 */
	virtual IAudioSettingContext* GetAudioSettings() = 0;

    /**
	 * @brief Gets video setting interface.
	 * @return If the function succeeds, the return value is an object pointer to IVideoSettingContext. Otherwise returns nullptr.
 	 */
	virtual IVideoSettingContext* GetVideoSettings() = 0;

    /**
	 * @brief Gets recording setting interface.
	 * @return If the function succeeds, the return value is an object pointer to IRecordingSettingContext. Otherwise returns nullptr.
	 */
	virtual IRecordingSettingContext* GetRecordingSettings() = 0;

    /**
	 * @brief Gets statistic settings interface.
	 * @return If the function succeeds, the return value is an object pointer to IStatisticSettingContext. Otherwise returns nullptr.
	 */
	virtual IStatisticSettingContext* GetStatisticSettings() = 0;

    /**
	 * @brief Gets share settings interface.
	 * @return If the function succeeds, the return value is an object pointer to IShareSettingContext. Otherwise returns nullptr.
 	 */
	virtual IShareSettingContext* GetShareSettings() = 0;

#if defined(WIN32)
    /**
	 * @brief Display Meeting Setting dialog.
	 * @param param Specify to display the Meeting Setting dialog.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 * @note Valid only for ZOOM style user interface mode.
 	 */
	virtual SDKError ShowSettingDlg(ShowSettingDlgParam& param) = 0;

    /**
	 * @brief Hide meeting setting dialog.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.  
	 * @note Valid only for ZOOM style user interface mode.
 	 */
	virtual SDKError HideSettingDlg() = 0;

    /**
	 * @brief Get Accessibility settings interface.
	 * @return If the function succeeds, the return value is an object pointer to IAccessibilitySettingContext. Otherwise returns nullptr.
 	 */
	virtual IAccessibilitySettingContext* GetAccessibilitySettings() = 0;

    /**
	 * @brief Gets setting user strategy interface.
	 * @return If the function succeeds, the return value is an object pointer to ISettingUIStrategy. Otherwise returns nullptr.
 	 */
	virtual ISettingUIStrategy* GetSettingUIStrategy() = 0;

    /**
	 * @brief Gets virtual background interface.
	 * @return If the function succeeds, the return value is an object pointer to IVirtualBGSettingContext. Otherwise returns nullptr.
 	 */
	virtual IVirtualBGSettingContext* GetVirtualBGSettings() = 0;

    /**
	 * @brief Gets video filter settings interface.
	 * @return If the function succeeds, the return value is an object pointer to IVideoFilterSettingContext. Otherwise returns nullptr.
 	 */
	virtual IVideoFilterSettingContext* GetVideoFilterSettings() = 0;

    /**
	 * @brief Get 3D avatar settings interface.
	 * @return If the function succeeds, the return value is an object pointer to I3DAvatarSettingContext. Otherwise returns nullptr.
 	 */
	virtual I3DAvatarSettingContext* Get3DAvatarSettings() = 0;

    /**
	 * @brief Gets face makeup settings interface.
	 * @return If the function succeeds, the return value is an object pointer to IFaceMakeupSettingContext. Otherwise returns nullptr.
 	 */
	virtual IFaceMakeupSettingContext* GetFaceMakeupSettings() = 0;
#endif

    /**
	 * @brief Gets wallpaper settings interface.
	 * @return If the function succeeds, the return value is an object pointer to IWallpaperSettingContext. Otherwise returns nullptr.
 	 */
	virtual IWallpaperSettingContext* GetWallpaperSettings() = 0;

};
END_ZOOM_SDK_NAMESPACE
#endif