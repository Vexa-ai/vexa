/**
 * @file zoom_sdk_def.h
 * @brief ZOOM windows SDK Common Definition File.
 */
#ifndef _ZOOM_SDK_DEF_H_
#define _ZOOM_SDK_DEF_H_
#if defined(WIN32)
#include <tchar.h>
typedef wchar_t zchar_t;
#define TLS_KEY_DEF uint32_t
#else
#include <stdint.h>
#include <unistd.h>
#include <cstring>
#include <float.h>
typedef char zchar_t;
typedef uint64_t UINT64;
typedef int64_t INT64;
typedef float  FLOAT;
typedef void* HWND;
#define TLS_KEY_DEF pthread_key_t
#endif//#if defined(WIN32)
#define PLATFORM_IMPORT	__declspec(dllimport)  
#define PLATFORM_EXPORT	__declspec(dllexport)
#ifdef ZOOM_SDK_DLL_EXPORT
#define SDK_API PLATFORM_EXPORT
#elif defined ZOOM_SDK_DLL_IMPORT
#define SDK_API PLATFORM_IMPORT
#else
#define SDK_API
#endif

#define ZOOM_SDK_NAMESPACE ZOOMSDK
#define BEGIN_ZOOM_SDK_NAMESPACE namespace ZOOM_SDK_NAMESPACE {
#define END_ZOOM_SDK_NAMESPACE };
#define USING_ZOOM_SDK_NAMESPACE using namespace ZOOM_SDK_NAMESPACE;

BEGIN_ZOOM_SDK_NAMESPACE
/**
 * @brief Enumeration of common errors of SDK. 
 */ 
enum SDKError
{
	/** Success. */
	SDKERR_SUCCESS = 0,
	/** The feature is currently invalid. */
	SDKERR_NO_IMPL,
	/** Incorrect usage of the feature. */
	SDKERR_WRONG_USAGE,
	/** Wrong parameter. */
	SDKERR_INVALID_PARAMETER,
	/** Loading module failed. */
	SDKERR_MODULE_LOAD_FAILED,
	/** No memory is allocated. */
	SDKERR_MEMORY_FAILED,
	/** Internal service error. */
	SDKERR_SERVICE_FAILED,
	/** Not initialized before the usage. */
	SDKERR_UNINITIALIZE,
	/** Not authorized before the usage. */
	SDKERR_UNAUTHENTICATION,
	/** No recording in process. */
	SDKERR_NORECORDINGINPROCESS,
	/** Transcoder module is not found. */
	SDKERR_TRANSCODER_NOFOUND,
	/** The video service is not ready. */
	SDKERR_VIDEO_NOTREADY,
	/** No permission. */
	SDKERR_NO_PERMISSION,
	/** Unknown error. */
	SDKERR_UNKNOWN,
	/** The other instance of the SDK is in process. */
	SDKERR_OTHER_SDK_INSTANCE_RUNNING,
	/** SDK internal error. */
	SDKERR_INTERNAL_ERROR,
	/** No audio device found. */
	SDKERR_NO_AUDIODEVICE_ISFOUND,
	/** No video device found. */
	SDKERR_NO_VIDEODEVICE_ISFOUND,
	/** API calls too frequently. */
	SDKERR_TOO_FREQUENT_CALL,
	/** User can't be assigned with new privilege. */
	SDKERR_FAIL_ASSIGN_USER_PRIVILEGE,
	/** The current meeting doesn't support the feature. */
	SDKERR_MEETING_DONT_SUPPORT_FEATURE,
	/** The current user is not the presenter. */
	SDKERR_MEETING_NOT_SHARE_SENDER,
	/** There is no sharing. */
	SDKERR_MEETING_YOU_HAVE_NO_SHARE,
	/** Incorrect ViewType parameters. */
	SDKERR_MEETING_VIEWTYPE_PARAMETER_IS_WRONG,
	/** Annotation is disabled. */
	SDKERR_MEETING_ANNOTATION_IS_OFF,
	/** Current OS doesn't support the setting. */
	SDKERR_SETTING_OS_DONT_SUPPORT,
	/** Email login is disable. */
	SDKERR_EMAIL_LOGIN_IS_DISABLED,
	/** Computer doesn't meet the minimum requirements to use virtual background feature. */
	SDKERR_HARDWARE_NOT_MEET_FOR_VB,
	/** Need process disclaimer. */
	SDKERR_NEED_USER_CONFIRM_RECORD_DISCLAIMER,
	/** There is no raw data of sharing. */
	SDKERR_NO_SHARE_DATA,
	/** Share can not subscribe myself. */
	SDKERR_SHARE_CANNOT_SUBSCRIBE_MYSELF,
	/** Not in meeting. */
	SDKERR_NOT_IN_MEETING,
	/** Not Joining audio. */
	SDKERR_NOT_JOIN_AUDIO,
	/** The current device doesn't support the feature. */
	SDKERR_HARDWARE_DONT_SUPPORT,
	/** Domain not support. */
	SDKERR_DOMAIN_DONT_SUPPORT,
	/** Remote control is disabled. */
	SDKERR_MEETING_REMOTE_CONTROL_IS_OFF,
	/** File transfer fail. */
	SDKERR_FILETRANSFER_ERROR,

};

/**
 * @brief Enumeration of the text resource type used by the SDK.
 */
enum SDK_LANGUAGE_ID
{
	/** For initialization. */
	LANGUAGE_Unknown = 0,
	/** In English. */
	LANGUAGE_English,
	/** In simplified Chinese. */
	LANGUAGE_Chinese_Simplified,
	/** In traditional Chinese. */
	LANGUAGE_Chinese_Traditional,
	/** In Japanese. */
	LANGUAGE_Japanese,
	/** In Spanish. */
	LANGUAGE_Spanish,
	/** In German. */
	LANGUAGE_German,
	/** In French. */
	LANGUAGE_French,
	/** In Portuguese. */
	LANGUAGE_Portuguese,
	/** In Russian. */
	LANGUAGE_Russian,
	/** In Korean. */
	LANGUAGE_Korean,
	/** In Vietnamese. */
	LANGUAGE_Vietnamese,
	/** In Italian. */
	LANGUAGE_Italian,
	/** In Polish. */
	LANGUAGE_Polish,
	/** In Turkish. */
	LANGUAGE_Turkish,
	/** In Indonesian. */
	LANGUAGE_Indonesian,
	/** In Dutch. */
	LANGUAGE_Dutch,
	/** In Swedish. */
	LANGUAGE_Swedish
};

/**
 * @brief Enumeration of memory modes for raw data handling.
 */
enum ZoomSDKRawDataMemoryMode 
{
	/** Use stack memory. */
	ZoomSDKRawDataMemoryModeStack,
	/** Use heap memory. */
	ZoomSDKRawDataMemoryModeHeap
};

#if (defined WIN32 )
/**
 * @brief The position of the window. The coordinate of position is that of monitor when the parent window is null. If the the parent window is not null, the position coordinate is that of the parent window.
 */
typedef struct tagWndPosition 
{
	/** Specifies the X-axis coordinate of the top-left corner of the window. */
	int left;
	/** Specifies the Y-axis coordinate of the top-left of the window. */
	int top;
	/** Specifies the window handle of the window itself. */
	HWND hSelfWnd;
	/** Specifies the window handle of the parent window. If the value is nullptr, the position coordinate is the monitor coordinate. */
	HWND hParent;
	tagWndPosition()
	{
		left = 0;
		top = 0;
		hSelfWnd = nullptr;
		hParent = nullptr;
	}
}WndPosition;

/**
 * @brief Enumeration of custom resource type used by the SDK.
 */
enum CustomizedLanguageType
{
	/** No use of the custom resource. */
	CustomizedLanguage_None,
	/** Use the specified file path to assign the custom resource. */
	CustomizedLanguage_FilePath,
	/** Use the specified content to assign the custom resource. */
	CustomizedLanguage_Content,
};

/**
 * @brief The custom resource information used by the SDK.
 */ 
typedef struct tagCustomizedLanguageInfo
{
	/** Resource name. */
	const char* langName;
	/** The value should be the full path of the resource file when the langType value is CustomizedLanguage_FilePath, including the file name. When the langType value is CustomizedLanguage_Content, the value saves the content of the resource. */
	const char* langInfo;
	/** Use the custom resource type. */
	CustomizedLanguageType langType;
	tagCustomizedLanguageInfo()
	{
		langName = nullptr;
		langInfo = nullptr;
		langType = CustomizedLanguage_None;
	}

}CustomizedLanguageInfo;

/**
 * @brief SDK configuration options.
 * @note This structure is used only for the SDK initialization to configure the custom resource file and choose whether to use the UI mode.
 */
#define ENABLE_CUSTOMIZED_UI_FLAG (1 << 5)

/**
 * @brief Enumeration of locale type.
 */
enum SDK_APP_Locale
{
	SDK_APP_Locale_Default,
	SDK_APP_Locale_CN,
};

/**
 * @brief Enumeration of video render mode. For more information, please visit <https://support.zoom.com/hc/en/article?id=zm_kb&sysparm_article=KB0066515.>. 
 */
enum ZoomSDKVideoRenderMode
{
	/** For initialization. */
	ZoomSDKVideoRenderMode_None = 0,
	/** Auto mode. */
	ZoomSDKVideoRenderMode_Auto,
	/** D3D11EnableFLIP mode. */
	ZoomSDKVideoRenderMode_D3D11EnableFLIP,
	/** D3D11 mode. */
	ZoomSDKVideoRenderMode_D3D11,
	/** D3D9 mode. */
	ZoomSDKVideoRenderMode_D3D9,
	/** GDI mode. */
	ZoomSDKVideoRenderMode_GDI,
};

/**
 * @brief Enumeration of render post processing mode. For more information, please visit <https://support.zoom.com/hc/en/article?id=zm_kb&sysparm_article=KB0066515.>.
 */
enum ZoomSDKRenderPostProcessing
{
	/** For initialization. */
	ZoomSDKRenderPostProcessing_None = 0,
	/** Auto mode. */
	ZoomSDKRenderPostProcessing_Auto,
	/** Enable post processing. */
	ZoomSDKRenderPostProcessing_Enable,
	/** Disable post processing. */
	ZoomSDKRenderPostProcessing_Disable,
};

/**
 * @brief Enumeration of video capturing mode. For more information, please visit <https://support.zoom.com/hc/en/article?id=zm_kb&sysparm_article=KB0066515.>.
 */
enum ZoomSDKVideoCaptureMethod
{
	/** For initialization. */
	ZoomSDKVideoCaptureMethod_None = 0,
	/** Auto mode. */
	ZoomSDKVideoCaptureMethod_Auto,
	/** Direct show mode. */
	ZoomSDKVideoCaptureMethod_DirectSHow,
	/** Media foundation mode. */
	ZoomSDKVideoCaptureMethod_MediaFoundation,
};

/**
 * @brief Video render options. For more information, please visit <https://support.zoom.com/hc/en/article?id=zm_kb&sysparm_article=KB0066515.>.
 */
typedef struct tagZoomSDKRenderOptions
{
	/** Video render mode. */
	ZoomSDKVideoRenderMode    videoRenderMode;
	/** Render post precessing mode. */
	ZoomSDKRenderPostProcessing renderPostProcessing;
	/** Video capture method. */
	ZoomSDKVideoCaptureMethod videoCaptureMethod;
	tagZoomSDKRenderOptions()
	{
		videoRenderMode = ZoomSDKVideoRenderMode_None;
		renderPostProcessing = ZoomSDKRenderPostProcessing_Auto;
		videoCaptureMethod = ZoomSDKVideoCaptureMethod_Auto;
	}
}ZoomSDKRenderOptions;
#endif

typedef struct tagConfigurableOptions
{
#if defined(WIN32)
	/** The custom resource information. */
	CustomizedLanguageInfo customizedLang;
	/** Additional functional configuration. The function currently supports whether to use the custom UI mode only. When the value of the optionalFeatures&ENABLE_CUSTOMIZED_UI_FLAG is true, it means the custom UI mode will be used. Otherwise the Zoom UI mode will be used. */
	int optionalFeatures;
#endif
	const zchar_t* sdkPathPostfix;
	tagConfigurableOptions()
	{
	#if defined(WIN32)
		optionalFeatures = 0;
	#endif
		sdkPathPostfix = nullptr;
	}

}ConfigurableOptions;

/**
 * @brief Rawdata options.
 */
typedef struct tagRawDataOptions
{
	/** false -- YUV420data, true -- intermediate data. */
	bool enableRawdataIntermediateMode; 
	/** Video rawdata memory mode. */
	ZoomSDKRawDataMemoryMode  videoRawdataMemoryMode;
	/** Share rawdata memory mode. */
	ZoomSDKRawDataMemoryMode  shareRawdataMemoryMode;
	/** Audio rawdata memory mode. */
	ZoomSDKRawDataMemoryMode  audioRawdataMemoryMode;
	tagRawDataOptions()
	{
		enableRawdataIntermediateMode = false;
		videoRawdataMemoryMode = ZoomSDKRawDataMemoryModeStack;
		shareRawdataMemoryMode = ZoomSDKRawDataMemoryModeStack;
		audioRawdataMemoryMode = ZoomSDKRawDataMemoryModeStack;
	}
}RawDataOptions;

/**
 * @brief Initialize the SDK Parameter.
 */
typedef struct tagInitParam  
{
	/** Web domain. */
	const zchar_t* strWebDomain;
	/** Branding name. */
	const zchar_t* strBrandingName;
	/** Support URL. */
	const zchar_t* strSupportUrl;
	/** The ID of the SDK language. */
	SDK_LANGUAGE_ID emLanguageID;
	/** Enable generate dump file if the app crashed. */
	bool enableGenerateDump; 
	/** Enable log feature. */
	bool enableLogByDefault;
	/** Size of a log file in M(megabyte). The default size is 5M. There are 5 log files in total and the file size varies from 1M to 50M. */
	unsigned int uiLogFileSize; 
	/** rawdata options. */
	RawDataOptions rawdataOpts;
	/** The configuration options of the SDK. */
	ConfigurableOptions obConfigOpts;
#if defined(WIN32)
	/** Resource module handle. */
	void* hResInstance;
	/** The ID of the small icon on the window. */
	unsigned int uiWindowIconSmallID;
	/** The ID of the big Icon on the window. */
	unsigned int uiWindowIconBigID;
	/** Locale. */
	SDK_APP_Locale locale;
	/** Render options. */
	ZoomSDKRenderOptions renderOpts;
	/** Per-monitor awareness mode. */
	bool permonitor_awareness_mode;
#endif
	/** Wrapper type. */
	int wrapperType;
	tagInitParam()
	{
		strWebDomain = nullptr;
		strBrandingName = nullptr;
		strSupportUrl = nullptr;
		emLanguageID = LANGUAGE_Unknown;
		enableGenerateDump = false;
		enableLogByDefault = false;
		uiLogFileSize = 5;
		wrapperType = 0;
#if defined(WIN32)
		hResInstance = (void*)-1;
		uiWindowIconSmallID = 0;
		uiWindowIconBigID = 0;
		locale = SDK_APP_Locale_Default;
		permonitor_awareness_mode = true;
#endif
	}
}InitParam;

/**
 * @brief Enumeration of types of shared content. 
 */
enum ShareType
{
	/** Type unknown. */
	SHARE_TYPE_UNKNOWN,
	/** Type of sharing the application. */
	SHARE_TYPE_AS,
	/** Type of sharing the desktop. */
	SHARE_TYPE_DS,
	/** Type of sharing the white-board. */
	SHARE_TYPE_WB,
	/** Type of sharing data from the device connected WIFI. */
	SHARE_TYPE_AIRHOST,
	/** Type of sharing the camera. */
	SHARE_TYPE_CAMERA,
	/** Type of sharing the data. */
	SHARE_TYPE_DATA,
	/** Type of sharing the video file. */
	SHARE_TYPE_VIDEO_FILE,
	/** Type of sharing the frame. */
	SHARE_TYPE_FRAME,
	/** Type of sharing the document. */
	SHARE_TYPE_DOCUMENT,
	/** Type of sharing the computer audio. */
	SHARE_TYPE_COMPUTER_AUDIO
};

/**
 * @brief Enumeration of the last error types of the SDK.
 */
enum LastErrorType
{
	/** No error. */
	LastErrorType_None,
	/** Error during verification. */
	LastErrorType_Auth,
	/** Error during login. */
	LastErrorType_Login,
	/** The associated error with the meeting. */
	LastErrorType_Meeting,
	/** The associated error with the SDK bottom layer. */
	LastErrorType_System,
};

/**
 * @brief Error mechanism interface provided by the SDK. This feature is gradually improved, so some errors may not be supported.
 */
class IZoomLastError
{
public:
    /**
	 * @brief Gets the last error type.	
	 * @return If the function succeeds, the error type will be returned.
	 */
	virtual LastErrorType GetErrorType() const = 0 ;
    /**
	 * @brief Gets the last error code.
	 * @return If the function succeeds, the error code will be returned.
	 */
	virtual UINT64 GetErrorCode() const = 0;

    /**
	 * @brief Gets the description for the last error.
	 * @return If the function succeeds, the error description will be returned. If there is no error, it will return an empty string of length zero(0).
	 */
	virtual const zchar_t* GetErrorDescription() const = 0;
	virtual ~IZoomLastError(){};
};
template<class T>
class IList
{
public:
	virtual ~IList(){};
	virtual int GetCount() = 0;
	virtual T   GetItem(int index) = 0;
	virtual void AddItem(T item) = 0;
};
#if (defined WIN32)
#define SDK_NULL_AUDIO_FILE_HANDLE (0xffffffff)
const RECT _SDK_TEST_VIDEO_INIT_RECT = {0,0,0,0};
#endif

/**
 * @brief Enumeration of video frame data format.
 */
enum FrameDataFormat
{
    /**I420 format with limited (TV) range color space. */
	FrameDataFormat_I420_LIMITED,
    /**I420 format with full (PC) range color space. */
	FrameDataFormat_I420_FULL,
};

/**
 * @brief Enumeration of the audio modes.
 */
enum ZoomSDKAudioChannel
{
    /**Single channel audio. */
	ZoomSDKAudioChannel_Mono,
    /**Stereo audio. */
	ZoomSDKAudioChannel_Stereo,
};

/**
 * @brief Enumeration of reasons why sharing is not allowed.
 */
enum CannotShareReasonType
{
    /**For initialization. */
	CannotShareReasonType_None,
    /**Only the host is allowed to share. */
	CannotShareReasonType_Locked,		                   
    /**Sharing is disabled. */
	CannotShareReasonType_Disabled,
    /**Another participant is currently sharing their screen. */
	CannotShareReasonType_Other_Screen_Sharing,
    /**Another participant is currently sharing their whiteboard. */
	CannotShareReasonType_Other_WB_Sharing,
    /**The user is sharing screen, and can grab. To grab, call EnableGrabShareWithoutReminder(true) before starting the share. */
	CannotShareReasonType_Need_Grab_Myself_Screen_Sharing,
    /**Another is sharing their screen, and can grab. To grab, call EnableGrabShareWithoutReminder(true) before starting share. */
	CannotShareReasonType_Need_Grab_Other_Screen_Sharing,
    /**Another is sharing pure computer audio, and can grab. To grab, call EnableGrabShareWithoutReminder(true) before starting share. */
	CannotShareReasonType_Need_Grab_Audio_Sharing,
    /**Other or myself is sharing whiteboard, and can Grab. To grab, call EnableGrabShareWithoutReminder(true) before starting share. */
	CannotShareReasonType_Need_Grap_WB_Sharing,
    /**The meeting has reached the maximum allowed screen share sessions. */
	CannotShareReasonType_Reach_Maximum,
    /**Other share screen in main session. */
	CannotShareReasonType_Have_Share_From_Mainsession,
    /**Another participant is sharing their zoom docs. */
	CannotShareReasonType_Other_DOCS_Sharing,
    /**Other or myself is sharing docs, and can grab. To grab, call EnableGrabShareWithoutReminder(true) before starting share. */
	CannotShareReasonType_Need_Grab_DOCS_Sharing,
    /**UnKnown reason. */
	CannotShareReasonType_UnKnown,
};

/**
 * @brief Enumeration of sharing status.
 */
enum SharingStatus
{
    /**Begin to share by the user himself. */
	Sharing_Self_Send_Begin,
    /**Stop sharing by the user. */
	Sharing_Self_Send_End,
    /**Begin to share pure audio by the user himself. */
	Sharing_Self_Send_Pure_Audio_Begin,
    /**Stop sharing pure audio by the user. */
	Sharing_Self_Send_Pure_Audio_End,
    /**Others begin to share. */
	Sharing_Other_Share_Begin,
    /**Others stop sharing. */
	Sharing_Other_Share_End,
    /**Others begin to share pure audio. */
	Sharing_Other_Share_Pure_Audio_Begin,
    /**Others stop sharing pure audio. */
	Sharing_Other_Share_Pure_Audio_End,
    /**View the sharing of others. */
	Sharing_View_Other_Sharing,
    /**Pause sharing. */
	Sharing_Pause,
    /**Resume sharing. */
	Sharing_Resume,
};

/**
 * @brief Zoom SDK color. The standard RGB color model has a value range of 0-255.
 */
struct ZoomSDKColor {
    /**Font color R value. */
	unsigned int red = 0;
    /**Font color G value. */
	unsigned int green = 0;
    /**Font color B value. */
	unsigned int blue = 0;
};

/**
* @brief Enumeration of asset type.
*/
enum GrantCoOwnerAssetsType {
	/** For initialization. */
	GrantCoOwnerAssetsType_None,
	/** Summary. */
	GrantCoOwnerAssetsType_Summary,
	/** CloudRecording. */
	GrantCoOwnerAssetsType_CloudRecording
};

/**
 * @brief Asset information.
 */
typedef struct tagGrantCoOwnerAssetsInfo
{
	 /**asset type. */
	GrantCoOwnerAssetsType assetType;
	/**the lock status of the asset. */
	bool isAssetsLocked;
	/**Query if the asset is granted. */
	bool isGranted;
	/**Set whether to grant privilege to manage the asset.*/
	bool setGrant;
	tagGrantCoOwnerAssetsInfo()
	{
		assetType = GrantCoOwnerAssetsType_None;
		isAssetsLocked = false;
		isGranted = false;
		setGrant = false;
	}
}GrantCoOwnerAssetsInfo;
END_ZOOM_SDK_NAMESPACE

#endif