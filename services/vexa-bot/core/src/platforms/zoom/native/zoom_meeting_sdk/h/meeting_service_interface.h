/**
 * @file meeting_service_interface.h
 * @brief Meeting Service Interface
 */
#ifndef _MEETING_SERVICE_INTERFACE_H_
#define _MEETING_SERVICE_INTERFACE_H_
#include "zoom_sdk_def.h"
#if defined(WIN32)
class IZoomRealNameAuthMeetingHelper;
#endif
BEGIN_ZOOM_SDK_NAMESPACE
/**
 * @brief Enumeration of meeting status.
 * Here are more detailed structural descriptions.
 */
enum MeetingStatus
{
	/** No meeting is running. */
	MEETING_STATUS_IDLE,
	/** Connect to the meeting server status. */
	MEETING_STATUS_CONNECTING,
	/** Waiting for the host to start the meeting. */
	MEETING_STATUS_WAITINGFORHOST,
	/** Meeting is ready, in meeting status. */
	MEETING_STATUS_INMEETING,
	/** Disconnect the meeting server, leave meeting status. */
	MEETING_STATUS_DISCONNECTING,
	/** Reconnecting meeting server status. */
	MEETING_STATUS_RECONNECTING,
	/** Failed to connect the meeting server. */
	MEETING_STATUS_FAILED,
	/** Meeting ends. */
	MEETING_STATUS_ENDED,
	/** Unknown status. */
	MEETING_STATUS_UNKNOWN,
	/** Meeting is locked to prevent the further participants to join the meeting. */
	MEETING_STATUS_LOCKED,
	/** Meeting is open and participants can join the meeting. */
	MEETING_STATUS_UNLOCKED,
	/** Participants who join the meeting before the start are in the waiting room. */
	MEETING_STATUS_IN_WAITING_ROOM,
	/** Upgrade the attendees to panelist in webinar. */
	MEETING_STATUS_WEBINAR_PROMOTE,
	/** Downgrade the attendees from the panelist. */
	MEETING_STATUS_WEBINAR_DEPROMOTE,
	/** Join the breakout room. */
	MEETING_STATUS_JOIN_BREAKOUT_ROOM,
	/** Leave the breakout room. */
	MEETING_STATUS_LEAVE_BREAKOUT_ROOM,
};

/**
 * @brief Enumeration of meeting failure code.
 * Here are more detailed structural descriptions.
 */
enum MeetingFailCode
{
	/** Start meeting successfully. */
	MEETING_SUCCESS							= 0,
	/** The connection with the backend service has errors. */
	MEETING_FAIL_CONNECTION_ERR             = 1,
	/** Reconnect error. */
	MEETING_FAIL_RECONNECT_ERR				= 2,
	/** Multi-media Router error. */
	MEETING_FAIL_MMR_ERR					= 3,
	/** Password is wrong. */
	MEETING_FAIL_PASSWORD_ERR				= 4,
	/** Session error. */
	MEETING_FAIL_SESSION_ERR				= 5,
	/** Meeting is over. */
	MEETING_FAIL_MEETING_OVER				= 6,
	/** Meeting has not begun. */
	MEETING_FAIL_MEETING_NOT_START			= 7,
	/** Meeting does not exist. */
	MEETING_FAIL_MEETING_NOT_EXIST			= 8,
	/** The capacity of meeting is full. For users that can't join meeting, they can go to watch live stream with the callback IMeetingServiceEvent::onMeetingFullToWatchLiveStream if the host has started. */
	MEETING_FAIL_MEETING_USER_FULL			= 9,
	/** The client is incompatible. */
	MEETING_FAIL_CLIENT_INCOMPATIBLE		= 10,
	/** The Multi-media router is not founded.  */
	MEETING_FAIL_NO_MMR						= 11,
	/** The meeting is locked. */
	MEETING_FAIL_CONFLOCKED					= 12,
	/** The meeting is failed because of the restriction by the same account. */
	MEETING_FAIL_MEETING_RESTRICTED			= 13,
	/** The meeting is restricted by the same account while the attendee is allowed to join before the host. */
	MEETING_FAIL_MEETING_RESTRICTED_JBH		= 14,
	/** Unable to send web request. */
	MEETING_FAIL_CANNOT_EMIT_WEBREQUEST		= 15,
	/** The token is expired. */
	MEETING_FAIL_CANNOT_START_TOKENEXPIRE	= 16,
	/** Video hardware or software error. */
	SESSION_VIDEO_ERR						= 17,
	/** Audio autostart error. */
	SESSION_AUDIO_AUTOSTARTERR				= 18,
	/** The number of webinar registered has reached the upper limit. */
	MEETING_FAIL_REGISTERWEBINAR_FULL		= 19,
	/** Register webinar with the role of webinar host. */
	MEETING_FAIL_REGISTERWEBINAR_HOSTREGISTER		= 20,
	/** Register webinar with the role of panelist member. */
	MEETING_FAIL_REGISTERWEBINAR_PANELISTREGISTER	= 21,
	/** Register webinar with the denied email. */
	MEETING_FAIL_REGISTERWEBINAR_DENIED_EMAIL		= 22,
	/** Webinar request to login. */
	MEETING_FAIL_ENFORCE_LOGIN		= 23,
	/** Invalid for Windows SDK. */
	CONF_FAIL_ZC_CERTIFICATE_CHANGED		= 24,  
	/** Vanity conference ID does not exist. */
	CONF_FAIL_VANITY_NOT_EXIST				= 27, 
	/** Join webinar with the same email. */
	CONF_FAIL_JOIN_WEBINAR_WITHSAMEEMAIL		= 28, 
	/** Meeting settings is not allowed to start a meeting. */
	CONF_FAIL_DISALLOW_HOST_MEETING		= 29, 
	/** Disabled to write the configure file. */
	MEETING_FAIL_WRITE_CONFIG_FILE			= 50,	
	/** Forbidden to join the internal meeting. */
	MEETING_FAIL_FORBID_TO_JOIN_INTERNAL_MEETING = 60, 
	/** Removed by the host.  */
	CONF_FAIL_REMOVED_BY_HOST = 61, 
	/** Forbidden to join meeting */
	MEETING_FAIL_HOST_DISALLOW_OUTSIDE_USER_JOIN = 62,   
	/** To join a meeting hosted by an external Zoom account, your SDK app has to be published on Zoom Marketplace. You can refer to Section 6.1 of Zoom's API License Terms of Use. */
	MEETING_FAIL_UNABLE_TO_JOIN_EXTERNAL_MEETING = 63,  
	/** Join failed because this Meeting SDK key is blocked by the host's account admin. */
	MEETING_FAIL_BLOCKED_BY_ACCOUNT_ADMIN = 64,  
	/** Need sign in using the same account as the meeting organizer. */
	MEETING_FAIL_NEED_SIGN_IN_FOR_PRIVATE_MEETING = 82,  
	/** Join meeting param vanityID is duplicated and needs to be confirmed.For more information about Vanity URLs, see https://support.zoom.com/hc/en/article?id=zm_kb&sysparm_article=KB0061540#multipleVanity */
	MEETING_FAIL_NEED_CONFIRM_PLINK = 88,
	/** Join meeting param vanityID does not exist in the current account.For more information about Vanity URLs, see https://support.zoom.com/hc/en/article?id=zm_kb&sysparm_article=KB0061540#multipleVanity */
	MEETING_FAIL_NEED_INPUT_PLINK = 89,
	/** App join token error. */
	MEETING_FAIL_APP_PRIVILEGE_TOKEN_ERROR = 500,  
	/** Authorized user not in meeting. */
	MEETING_FAIL_AUTHORIZED_USER_NOT_INMEETING = 501,
	/** On-behalf token error: conflict with login credentials. */
	MEETING_FAIL_ON_BEHALF_TOKEN_CONFLICT_LOGIN_ERROR = 502,
	/** Jmak user email not match */
	MEETING_FAIL_JMAK_USER_EMAIL_NOT_MATCH = 1143, 
	MEETING_FAIL_UNKNOWN = 0xffff,

};  

/**
 * @brief Enumeration of meeting end reason.
 * Here are more detailed structural descriptions.
 */
enum MeetingEndReason
{
	/** For initialization. */
	EndMeetingReason_None = 0,
	/** Kicked by host. */
	EndMeetingReason_KickByHost = 1,
	/** Ended by host. */
	EndMeetingReason_EndByHost = 2,
	/** JBH times out. */
	EndMeetingReason_JBHTimeOut = 3,
	/** No attendee. */
	EndMeetingReason_NoAttendee = 4,
	/** Host starts another meeting. */
	EndMeetingReason_HostStartAnotherMeeting = 5,
	/** Free meeting times out. */
	EndMeetingReason_FreeMeetingTimeOut = 6,
	/** Represents an undefined end meeting reason, typically used for new error codes introduced by the backend after client release */
	EndMeetingReason_Undefined = 7,
	/* Authorized user left. */
	EndMeetingReason_DueToAuthorizedUserLeave = 8,
};

/**
 * @brief Enumeration of meeting type.
 * Here are more detailed structural descriptions.
 */
enum MeetingType
{
	/** For initialization. */
	MEETING_TYPE_NONE,
	/** Ordinary meeting. */
	MEETING_TYPE_NORMAL,
	/** Webinar. */
	MEETING_TYPE_WEBINAR,
	/** Breakout meeting. */
	MEETING_TYPE_BREAKOUTROOM,
};

/**
 * @brief Enumeration of leave meeting command.
 * Here are more detailed structural descriptions.
 */
enum LeaveMeetingCmd
{
	/** Leave meeting */
	LEAVE_MEETING,
	/** End meeting */
	END_MEETING,
};

/**
 * @brief Enumeration of SDK user type.
 * Here are more detailed structural descriptions.
 */
enum SDKUserType
{
	/** Type of ordinary user who needs to login. */
	SDK_UT_NORMALUSER = 100,
	/** Start meeting without login. */
	SDK_UT_WITHOUT_LOGIN,
};

/**
 * @brief Enumeration of raw audio data sampling rate.
 * Here are more detailed structural descriptions.
 */
enum AudioRawdataSamplingRate
{
	/** The sampling rate of the acquired raw audio data is 32K. */
	AudioRawdataSamplingRate_32K, 
	/** The sampling rate of the acquired raw audio data is 48K. */
	AudioRawdataSamplingRate_48K, 
};

/**
 * @brief Enumeration of video rawdata colorspace.
 * Here are more detailed structural descriptions.
 */
enum VideoRawdataColorspace
{
	/** For standard definition TV (SDTV)  Y[16,235], Cb/Cr[16,240]. */
	VideoRawdataColorspace_BT601_L,
	/** For standard definition TV (SDTV) full range version: [0,255]. */
	VideoRawdataColorspace_BT601_F,
	/** For high definition TV (HDTV) Y[16,235], Cb/Cr[16,240] */
	VideoRawdataColorspace_BT709_L,
	/** For high definition TV (HDTV) full range version: [0,255] */
	VideoRawdataColorspace_BT709_F
};

/**
 * @brief The parameters of non-login user when joins the meeting.
 * Here are more detailed structural descriptions.
 */
typedef struct tagJoinParam4WithoutLogin
{
	/**  Meeting number. */
	UINT64		   meetingNumber;
	/** Meeting vanity ID */
	const zchar_t* vanityID;
	/** Username when logged in the meeting. */
	const zchar_t* userName;
	/** Meeting password. */
	const zchar_t* psw;
	/** app_privilege_token. */
	const zchar_t* app_privilege_token; 
	/** ZOOM access token. */
	const zchar_t* userZAK;
	/** The customer key that need the app intergrated with sdk to specify. The SDK will set this value when the associated settings are turned on. The max length of customer_key is 35. */
	const zchar_t* customer_key;
	/** Webinar token. */
	const zchar_t* webinarToken;
	/** Turn off the video of not. true indicates to turn off. In addition, this flag is affected by meeting attributes. */
	bool		   isVideoOff;
	/** Turn off the audio or not. true indicates to turn off. In addition, this flag is affected by meeting attributes. */
	bool		   isAudioOff;
	/** Join token. */
	const zchar_t* join_token;
	/** On behalf token. */
	const zchar_t* onBehalfToken;
	/** Is my voice in the mixed audio raw data? */
	bool           isMyVoiceInMix; 
#if defined(WIN32)
	/** The window handle of the direct Sharing application. */
	HWND		   hDirectShareAppWnd;
	/** Share the desktop directly or not. true indicates to share. */
	bool		   isDirectShareDesktop;
#endif
	/** Is audio raw data stereo? The default is mono. */
	bool           isAudioRawDataStereo; 
	/** The sampling rate of the acquired raw audio data. The default is AudioRawdataSamplingRate_32K. */
	AudioRawdataSamplingRate eAudioRawdataSamplingRate; 
	/** The colorspace of video rawdata. The default is VideoRawdataColorspace_BT601_L. */
	VideoRawdataColorspace eVideoRawdataColorspace;
}JoinParam4WithoutLogin;

/**
 * @brief The parameter of ordinary logged-in user.
 * Here are more detailed structural descriptions.
 */
typedef struct tagJoinParam4NormalUser
{
	/** Meeting number. */
	UINT64		   meetingNumber;
	/** Meeting vanity ID. */
	const zchar_t* vanityID;
	/** Username when logged in the meeting. */
	const zchar_t* userName;
	/** Meeting password. */
	const zchar_t* psw;
	/** app_privilege_token. */
	const zchar_t* app_privilege_token; 
	/** The customer key that need the app intergrated with sdk to specify. The SDK will set this value when the associated settings are turned on. The max length of customer_key is 35. */
	const zchar_t* customer_key;
	/** Webinar token. */
	const zchar_t* webinarToken;
	/** Turn off the video or not. true indicates to turn off. In addition, this flag is affected by meeting attributes. */
	bool		   isVideoOff;
	/** Turn off the audio or not. true indicates to turn off. In addition, this flag is affected by meeting attributes. */
	bool		   isAudioOff;
	/** Join token. */
	const zchar_t* join_token;
	/** Is my voice in the mixed audio raw data? */
	bool           isMyVoiceInMix;
#if defined(WIN32)
	/** The window handle of the direct sharing application. */
	HWND		   hDirectShareAppWnd;
	/** Share the desktop directly or not. true indicates to share. */
	bool		   isDirectShareDesktop;
#endif
	/** Is audio raw data stereo? The default is mono. */
	bool           isAudioRawDataStereo; 
	/** The sampling rate of the acquired raw audio data. The default is AudioRawdataSamplingRate_32K. */
	AudioRawdataSamplingRate eAudioRawdataSamplingRate;
	/** The colorspace of video rawdata. The default is VideoRawdataColorspace_BT601_L. */
	VideoRawdataColorspace eVideoRawdataColorspace;
	
}JoinParam4NormalUser;

/**
 * @brief The way and the parameter of the users when join the meeting.
 * Here are more detailed structural descriptions.
 */
typedef struct tagJoinParam
{
	/** User type. */
	SDKUserType userType;
	union 
	{
	/** The parameter of ordinary user when joins the meeting. */
		JoinParam4NormalUser normaluserJoin;
	/** The parameters of unlogged-in user when joins the meeting. */
		JoinParam4WithoutLogin withoutloginuserJoin;
	} param;    
	tagJoinParam()
	{
		userType = SDK_UT_WITHOUT_LOGIN;
		memset(&param, 0, sizeof(param));  /** checked safe */
	}
}JoinParam;


/**
 * @brief Enumeration of Zoom user type.
 * Here are more detailed structural descriptions.
 */
enum ZoomUserType
{
	/** API user. */
	ZoomUserType_APIUSER,
	/** User logged in with email. */
	ZoomUserType_EMAIL_LOGIN,
	/** User logged in with Facebook. */
	ZoomUserType_FACEBOOK,
	/** User logged in with Google. */
	ZoomUserType_GoogleOAuth,
	/** User logged in with SSO. */
	ZoomUserType_SSO,
	/** User of unknown type. */
	ZoomUserType_Unknown,
};

/**
 * @brief The parameter used by unlogged-in user when starts the meeting.
 * Here are more detailed structural descriptions.
 */
typedef struct tagStartParam4WithoutLogin
{
	/** ZOOM access token. */
	const zchar_t* userZAK;
	/** Username when logged in the meeting. */
	const zchar_t* userName;
	/** User type. */
	ZoomUserType   zoomuserType;
	/** Meeting number. */
	UINT64		   meetingNumber;
	/**  Meeting vanity ID */
	const zchar_t* vanityID;
	/** The customer key that need the app intergrated with sdk to specify. The SDK will set this value when the associated settings are turned on. The max length of customer_key is 35. */
	const zchar_t* customer_key;
	/** Turn off the video or not. true indicates to turn off. In addition, this flag is affected by meeting attributes. */
	bool		   isVideoOff;
	/** Turn off the audio or not. true indicates to turn off. In addition, this flag is affected by meeting attributes. */
	bool		   isAudioOff;
	/** Is my voice in the mixed audio raw data? */
	bool           isMyVoiceInMix; 
#if defined(WIN32)
	/** The window handle of the direct sharing application. */
	HWND		   hDirectShareAppWnd;
	/** Share the desktop directly or not. true indicates to share. */
	bool		   isDirectShareDesktop;
#endif
	/** Is audio raw data stereo? The default is mono. */
	bool           isAudioRawDataStereo; 
	/** The sampling rate of the acquired raw audio data. The default is AudioRawdataSamplingRate_32K. */
	AudioRawdataSamplingRate eAudioRawdataSamplingRate; 
	/** The colorspace of video rawdata. The default is VideoRawdataColorspace_BT601_L. */
	VideoRawdataColorspace eVideoRawdataColorspace;
}StartParam4WithoutLogin;

/**
 * @brief The parameter of ordinary user when starts meeting.
 * Here are more detailed structural descriptions.
 */
typedef struct tagStartParam4NormalUser
{
	/** Meeting number. */
	UINT64			meetingNumber;
	/** Meeting vanity ID. Generate a ZOOM access token via REST API. */
	const zchar_t*  vanityID;
	/** The customer key that need the app intergrated with sdk to specify. The SDK will set this value when the associated settings are turned on. The max length of customer_key is 35. */
	const zchar_t*  customer_key;
	/** Turn off video or not. true indicates to turn off. In addition, this flag is affected by meeting attributes. */
	bool		    isVideoOff;
	/** Turn off audio or not. true indicates to turn off. In addition, this flag is affected by meeting attributes. */
	bool		    isAudioOff;
	/** Is my voice in the mixed audio raw data? */
	bool            isMyVoiceInMix; 
#if defined(WIN32)
	/** The window handle of the direct sharing application. */
	HWND			hDirectShareAppWnd;
	/** Share the desktop directly or not. true indicates to share. */
	bool		    isDirectShareDesktop;
#endif
	/** Is audio raw data stereo? The default is mono. */
	bool            isAudioRawDataStereo; 
	/** The sampling rate of the acquired raw audio data. The default is AudioRawdataSamplingRate_32K. */
	AudioRawdataSamplingRate eAudioRawdataSamplingRate;
	/** The colorspace of video rawdata. The default is VideoRawdataColorspace_BT601_L. */
	VideoRawdataColorspace eVideoRawdataColorspace;
}StartParam4NormalUser;


/**
 * @brief The way and the parameter for meeting start.
 * Here are more detailed structural descriptions.
 */
typedef struct tagStartParam
{
	/** User type. */
	SDKUserType userType;
	const zchar_t* inviteContactId;
	union 
	{
	/** The parameter for ordinary user when starts the meeting. */
		StartParam4NormalUser normaluserStart;
	/** The parameter for unlogged-in user when starts the meeting.  */
		StartParam4WithoutLogin withoutloginStart;
	}param;    
	tagStartParam()
	{
		userType = SDK_UT_WITHOUT_LOGIN;
		inviteContactId = nullptr;
		memset(&param, 0, sizeof(param));  /** checked safe */
	}
}StartParam;

/**
 * @brief Enumeration of connection quality.
 * Here are more detailed structural descriptions.
 */
enum ConnectionQuality 
{
	/** Unknown connection status */
	Conn_Quality_Unknown,
	/** The connection quality is very poor. */
	Conn_Quality_Very_Bad,
	/** The connection quality is poor.  */
	Conn_Quality_Bad,
	/** The connection quality is not good. */
	Conn_Quality_Not_Good,
	/** The connection quality is normal. */
	Conn_Quality_Normal,
	/** The connection quality is good. */
	Conn_Quality_Good,
	/** The connection quality is excellent. */
	Conn_Quality_Excellent,
};

/**
 * @brief Enumeration of meeting component.
 * Here are more detailed structural descriptions.
 */
enum MeetingComponentType
{
	/** Default component type. */
	MeetingComponentType_Def = 0,
	/** Audio. */
	MeetingComponentType_AUDIO,
	/** Video. */
	MeetingComponentType_VIDEO,
	/** Share application. */
	MeetingComponentType_SHARE,
};

/**
 * @brief The meeting audio statistic information.
 */
typedef struct tagMeetingAudioStatisticInfo
{
	/** This meeting's sent audio frequency in kilohertz (KHz). */
	int   sendFrequency;
	/** This meeting's sent band width of audio. */
	int   sendBandwidth;
	/** This meeting's sent audio rtt. */
	int   sendRTT;
	/** This meeting's sent audio jitter. */
	int   sendJitter;
	/** This meeting's average of send audio packet loss. */
	float sendPacketLossAvg;
	/** This meeting's maximum send audio packet loss. */
	float sendPacketLossMax;

	/** This meeting's received audio frequency in kilohertz (KHz). */
	int   recvFrequency;
	/** This meeting's received band width of audio. */
	int   recvBandwidth;
	/** This meeting's received audio rtt. */
	int   recvRTT;
	/** This meeting's received audio jitter. */
	int   recvJitter;
	/** This meeting's average of received audio packet loss. */
	float recvPacketLossAvg;
	/** This meeting's maximum received audio packet loss. */
	float recvPacketLossMax;

	tagMeetingAudioStatisticInfo()
	{
		sendFrequency = 0;
		sendBandwidth = 0;
		sendRTT = 0;
		sendJitter = 0;
		sendPacketLossAvg = 0;
		sendPacketLossMax = 0;
		recvFrequency = 0;
		recvBandwidth = 0;
		recvRTT = 0;
		recvJitter = 0;
		recvPacketLossAvg = 0;
		recvPacketLossMax = 0;
		
	}
}MeetingAudioStatisticInfo;

/**
 * @brief The meeting video or share statistic information.
 */
typedef struct tagMeetingASVStatisticInfo
{
	/** This meeting's sent band-width for video or sharing. */
	int	  sendBandwidth;
	/** This meeting's sent frame rate for video or sharing. */
	int   sendFps;
	/** This meeting's sent video or sharing rtt data. */
	int   sendRTT;
	/** This meeting's sent video or sharing jitter data. */
	int   sendJitter;
	/** This meeting's sent video or sharing resolution. HIWORD->height, LOWORD->width. */
	int   sendResolution;
	/** This meeting's average video or sharing packet loss for sent data. */
	float sendPacketLossAvg;
	/** This meeting's maximum video or sharing packet loss for sent data. */
	float sendPacketLossMax;

	/** This meeting's received band-width for video or sharing. */
	int	  recvBandwidth;
	/** This meeting's received frame rate for video or sharing. */
	int   recvFps;
	/** This meeting's received video or sharing rtt data. */
	int   recvRTT;
	/** This meeting's received video or sharing jitter data. */
	int   recvJitter;
	/** This meeting's received video or sharing resolution. HIWORD->height, LOWORD->width. */
	int   recvResolution;
	/** This meeting's average video or sharing packet loss for received data. */
	float recvPacketLossAvg;
	/** This meeting's maximum video or sharing packet loss for received data. */
	float recvPacketLossMax;

	tagMeetingASVStatisticInfo()
	{
		sendFps = 0;
		sendBandwidth = 0;
		sendRTT = 0;
		sendJitter = 0;
		sendResolution = 0;
		sendPacketLossAvg = 0;
		sendPacketLossMax = 0;
		recvFps = 0;
		recvBandwidth = 0;
		recvRTT = 0;
		recvJitter = 0;
		recvResolution = 0;
		recvPacketLossAvg = 0;
		recvPacketLossMax = 0;
	}
}MeetingASVStatisticInfo;

#if defined(WIN32)
/**
 * @brief Enumeration of SDK view type, primary displayer and secondary displayer.
 * Here are more detailed structural descriptions.
 */
enum SDKViewType
{
	/** Primary displayer. */
	SDK_FIRST_VIEW,
	/** Secondary displayer. */
	SDK_SECOND_VIEW,
	
	SDK_SEND_SHARE_VIEW,
};

/**
 * @brief Enumeration of share view zoom ratio.
 * Here are more detailed structural descriptions.
 */
enum SDKShareViewZoomRatio
{
	SDK_ShareViewZoomRatio_50,
	SDK_ShareViewZoomRatio_100,
	SDK_ShareViewZoomRatio_150,
	SDK_ShareViewZoomRatio_200,
	SDK_ShareViewZoomRatio_300
};
#endif
/**
 * @brief Enumeration of meeting supported audio type.
 * Here are more detailed structural descriptions.
 */
enum InMeetingSupportAudioType
{
	AUDIO_TYPE_NONE = 0,
	AUDIO_TYPE_VOIP = 1,
	AUDIO_TYPE_TELEPHONY = 1 << 1
};


/**
 * @brief Enumeration of meeting connection type.
 * Here are more detailed structural descriptions.
 */
enum MeetingConnType
{
	/** Disconnection. */
	Meeting_Conn_None,
	/** Normal connection. */
	Meeting_Conn_Normal,
	/** Failure and reconnection. */
	Meeting_Conn_FailOver,
};

/**
 * @class IMeetingInfo
 * @brief Meeting information Interface.
 */
class IMeetingInfo
{
public:
	/**
	 * @brief Gets the current meeting number.
	 * @return If the function succeeds, the return value is the current meeting number. Otherwise returns ZERO(0).
	 */
	virtual UINT64 GetMeetingNumber() = 0;
	
	/**
	 * @brief Gets the current meeting ID.
	 * @return If the function succeeds, the return value is the current meeting ID. Otherwise returns an empty string of length ZERO(0).
	 */
	virtual const zchar_t* GetMeetingID() = 0;
	
	/**
	 * @brief Gets the meeting topic.
	 * @return If the function succeeds, the return value is the current meeting topic. Otherwise returns an empty string of length ZERO(0)
	 */
	virtual const zchar_t* GetMeetingTopic() = 0;
	
	/**
	 * @brief Gets the meeting password.
	 * @return If the function succeeds, the return value is the current meeting password. Otherwise returns an empty string of length ZERO(0)
	 */
	virtual const zchar_t* GetMeetingPassword() = 0;
	
	/**
	 * @brief Gets the meeting type.
	 * @return If the function succeeds, it returns the current meeting type. Otherwise, this function fails and returns nullptr.
	 */
	virtual MeetingType GetMeetingType() = 0;
	
	/**
	 * @brief Gets the email invitation template for the current meeting.
	 * @return If the function succeeds, the return value is the email invitation template. Otherwise returns nullptr.
	 */
	virtual const zchar_t* GetInviteEmailTemplate() = 0;
	
	/**
	 * @brief Gets the meeting title in the email invitation template.
	 * @return If the function succeeds, the return value is the meeting title. Otherwise returns nullptr.
	 */
	virtual const zchar_t* GetInviteEmailTitle() = 0;
	
	/**
	 * @brief Gets the URL of invitation to join the meeting.
	 * @return If the function succeeds, the return value is the URL of invitation. Otherwise returns nullptr.
	 */
	virtual const zchar_t* GetJoinMeetingUrl() = 0;
	
	/**
	 * @brief Gets the host tag of the current meeting.
	 * @return If the function succeeds, the return value is the host tag. Otherwise returns nullptr.
	 */
	virtual const zchar_t* GetMeetingHostTag() = 0;
	
	/**
	 * @brief Gets the connection type of the current meeting.
	 * @return The connection type.
	 */
	virtual MeetingConnType GetMeetingConnType() = 0;
	
	/**
	 * @brief Gets the audio type supported by the current meeting. see \link InMeetingSupportAudioType \endlink enum. 
	 * @return If the function succeeds, it will return the type. The value is the 'bitwise OR' of each supported audio type.
	 */
	virtual int GetSupportedMeetingAudioType() = 0;

	virtual ~IMeetingInfo(){};
};

/**
 * @brief Meeting parameter.
 * Here are more detailed structural descriptions.
 */
typedef struct tagMeetingParameter
{
	/** Meeting type. */
	MeetingType meeting_type;
	/** View only or not. true indicates to view only. */
	bool is_view_only;
	/** Auto local recording or not. true indicates to auto local recording. */
	bool is_auto_recording_local;
	/** Auto cloud recording or not. true indicates to auto cloud recording. */
	bool is_auto_recording_cloud;
	/** Meeting number. */
	UINT64 meeting_number;
	/** Meeting topic. */
	const zchar_t* meeting_topic;
	/** Meeting host. */
	const zchar_t* meeting_host;
	tagMeetingParameter()
	{
		meeting_type = MEETING_TYPE_NONE;
		is_view_only = true;
		is_auto_recording_local = false;
		is_auto_recording_cloud = false;
		meeting_number = 0;
		meeting_topic = nullptr;
		meeting_host = nullptr;
	}

	~tagMeetingParameter()
	{
		if (meeting_host)
		{
			delete[] meeting_host;
			meeting_host = nullptr;
		}
		if (meeting_topic)
		{
			delete[] meeting_topic;
			meeting_topic = nullptr;
		}
	}
}MeetingParameter;

/**
 * @brief Enumeration of meeting statistics warning type.
 * Here are more detailed structural descriptions.
 */
enum StatisticsWarningType
{
	/** No warning. */
	Statistics_Warning_None,
	/** The network connection quality is bad. */
	Statistics_Warning_Network_Quality_Bad,
	/** The system is busy. */
	Statistics_Warning_Busy_System,
};

#if defined(WIN32)
/**
 * @class IMeetingAppSignalHandler
 * @brief the interface to handle app signal panel in meeting.
 */
class IMeetingAppSignalHandler
{
public:
	virtual ~IMeetingAppSignalHandler() {};

	/**
	 * @brief Check if the app signal panel can be shown.
	 * @return true if the app signal panel can be shown, false otherwise.
	 */
	virtual bool CanShowPanel() = 0;
	/**
	 * @brief Show the app signal panel window.
	 * @param x The horizontal coordinate value.
	 * @param y The vertical coordinate value.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError ShowPanel(unsigned int x, unsigned int y) = 0;
	/**
	 * @brief Hide the app signal panel window.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError HidePanel() = 0;
};
#endif

/**
 * @class IMeetingServiceEvent
 * @brief Meeting service callback event.
 */
class IMeetingServiceEvent
{
public:
	virtual ~IMeetingServiceEvent() {}
	/**
	 * @brief Meeting status changed callback.
	 * @param status The value of meeting.
	 * @param iResult Detailed reasons for special meeting status.
	 * If the status is MEETING_STATUS_FAILED, the value of iResult is one of those listed in MeetingFailCode enum. 
	 * If the status is MEETING_STATUS_ENDED, the value of iResult is one of those listed in MeetingEndReason.
	 */
	virtual void onMeetingStatusChanged(MeetingStatus status, int iResult = 0) = 0;
	
	/**
	 * @brief Meeting statistics warning notification callback.
	 * @param type The warning type of the meeting statistics.
	 */
	virtual void onMeetingStatisticsWarningNotification(StatisticsWarningType type) = 0;
	
	/**
	 * @brief Meeting parameter notification callback.
	 * @param meeting_param Meeting parameter.
	 * @note The callback will be triggered right before the meeting starts. The meeting_param will be destroyed once the function calls end.
	 */
	virtual void onMeetingParameterNotification(const MeetingParameter* meeting_param) = 0;
	
	/**
	 * @brief Callback event when a meeting is suspended.
	 */
	virtual void onSuspendParticipantsActivities() = 0;
	
	/**
	 * @brief Callback event for the AI Companion active status changed. 
	 * @param active Specify whether the AI Companion active or not.
	 */
	virtual void onAICompanionActiveChangeNotice(bool bActive) = 0;
	
	/**
	 * @brief Callback event for the meeting topic changed. 
	 * @param sTopic The new meeting topic.
	 */
	virtual void onMeetingTopicChanged(const zchar_t* sTopic) = 0;
	
	/**
	 * @brief Calback event that the meeting users have reached the meeting capacity.
	 *  The new join user can not join meeting, but they can watch the meeting live stream.
	 * @param sLiveStreamUrl The live stream URL to watch the meeting live stream.
	 */
	virtual void onMeetingFullToWatchLiveStream(const zchar_t* sLiveStreamUrl) = 0;

	/**
	 * @brief Called when the user's share network quality changes.
	 * @param type The data type whose network quality changed.
	 * @param level The new network quality level for the specified data type.
	 * @param userId The user whose network quality changed.
	 * @param uplink This data is uplink or downlink.
	 */
	virtual void onUserNetworkStatusChanged(MeetingComponentType type, ConnectionQuality level, unsigned int userId, bool uplink) = 0;

#if defined(WIN32)
	/**
	 * @brief Callback event when the app signal panel is updated.
	 * @param handler The handler object to control the app signal panel.
	 * @note Only available for the custom UI.
	 */
	virtual void onAppSignalPanelUpdated(IMeetingAppSignalHandler* pHandler) = 0;
#endif
};

/**
 * @class IListFactory
 * @brief IListFactory interface.
 */
class IListFactory {
public:
	virtual ~IListFactory() {}

	/**
	* @brief Creates a new list of GrantCoOwnerAssetsInfo objects.
	* @return IList<GrantCoOwnerAssetsInfo>* A pointer to a newly created list of GrantCoOwnerAssetsInfo.
	* @note The caller is responsible for destroying the list using DestroyAssetsInfoList to avoid memory leaks.
	*/
	virtual IList<GrantCoOwnerAssetsInfo>* CreateAssetsInfoList() = 0;
	/**
	* @brief Destroys a previously created list of GrantCoOwnerAssetsInfo objects.
	* @param list A pointer to the list to be destroyed.
	* @note This should only be called for lists created by CreateAssetsInfoList.
	*/
	virtual void DestroyAssetsInfoList(IList<GrantCoOwnerAssetsInfo>* list) = 0;
};	
#if defined(WIN32)
class IAnnotationController;
class IMeetingBreakoutRoomsController;
class IMeetingH323Helper;
class IMeetingPhoneHelper;
class IMeetingRemoteController;
class IMeetingUIController;
class IMeetingLiveStreamController;
class IClosedCaptionController;
class IMeetingQAController;
class IMeetingInterpretationController;
class IMeetingSignInterpretationController;
class IEmojiReactionController;
class IMeetingAANController;
class ICustomImmersiveController;
class IMeetingPollingController;
class IMeetingIndicatorController;
class IMeetingProductionStudioController;
#endif
class IMeetingConfiguration;
class IMeetingBOController;
class IMeetingChatController;
class IMeetingAudioController;
class IMeetingParticipantsController;
class IMeetingRecordingController;
class IMeetingShareController;
class IMeetingVideoController;
class IMeetingWaitingRoomController;
class IMeetingWebinarController;
class IMeetingRawArchivingController;
class IMeetingReminderController;
class IMeetingWhiteboardController;
class IMeetingSmartSummaryController;
class IMeetingEncryptionController;
class IMeetingRemoteSupportController;
class IMeetingAICompanionController;
class IMeetingDocsController;
/**
 * @class IMeetingService
 * @brief Meeting Service Interface
 */
class IMeetingService
{
public:
	/**
	 * @brief Sets meeting service callback event handler.
	 * @param pEvent A pointer to the IMeetingServiceEvent that receives the meeting service callback event.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError SetEvent(IMeetingServiceEvent* pEvent) = 0;
	
	/**
	 * @brief Joins meeting with web uri
	 * @param protocol_action Specifies the web uri
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError HandleZoomWebUriProtocolAction(const zchar_t* protocol_action) = 0;
	
	/**
	 * @brief Joins the meeting.
	 * @param joinParam The parameter is used to join meeting.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError Join(JoinParam& joinParam) = 0;
	
	/**
	 * @brief Starts meeting.
	 * @param startParam The parameter is used to start meeting.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError Start(StartParam& startParam) = 0;
	
	/**
	 * @brief Leaves meeting.
	 * @param leaveCmd Leave meeting command.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError Leave(LeaveMeetingCmd leaveCmd) = 0;
	
	/**
	 * @brief Gets meeting status.
	 * @return If the function succeeds, the return value is the current meeting status. 
	 */
	virtual MeetingStatus GetMeetingStatus() = 0;
	
	/**
	 * @brief Lock the current meeting.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError LockMeeting() = 0;
	
	/**
	 * @brief Unlock the current meeting.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError UnlockMeeting() = 0;
	
	/**
	 * @brief Determines if the meeting is locked.
	 * @return true indicates the meeting status is locked.
	 */
	virtual bool IsMeetingLocked() = 0;
	
	/**
	 * @brief Determines if the current user can change the meeting topic.
	 * @return If it can change the meeting topic, the return value is true.
	 */
	virtual bool CanSetMeetingTopic() = 0;
	
	/**
	 * @brief Change the meeting topic.
	 * @param sTopic The new meeting topic. 
	 * @return If the function succeeds, the return value is the SDKERR_SUCCESS. Otherwise fails.
	 */
	virtual SDKError SetMeetingTopic(const zchar_t* sTopic) = 0;
	
	/**
	 * @brief Suspend all participant activities.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError SuspendParticipantsActivities() = 0;
	
	/**
	 * @brief Determines if host/cohose can suspend participant activities.
	 * @return If it can suspend participant activities, the return value is true.
	 */
	virtual bool CanSuspendParticipantsActivities() = 0;
	
	/**
	 * @brief Gets meeting information.
	 * @return If the function succeeds, the return value is the meeting information. Otherwise returns nullptr.
	 */
	virtual IMeetingInfo* GetMeetingInfo() = 0;
	
	/**
	 * @brief Gets the quality of Internet connection when sharing.
	 * @param bSending true indicates to get the connection quality of sending the sharing statistics. false indicates to get the connection quality of receiving the sharing statistics.
	 * @return If the function succeeds, the return is one of those enumerated in ConnectionQuality enum.
	 * @note If you are not in the meeting, the Conn_Quality_Unknown will be returned.
	 */
	virtual ConnectionQuality GetSharingConnQuality(bool bSending = true) = 0;
	
	/**
	 * @brief Gets the Internet connection quality of video.
	 * @param bSending true indicates to get the connection quality of sending the video. false indicates to get the connection quality of receiving the video.
	 * @return If the function succeeds, the return is one of those enumerated in ConnectionQuality enum.
	 * @note If you are not in the meeting, the Conn_Quality_Unknown will be returned.
	 */
	virtual ConnectionQuality GetVideoConnQuality(bool bSending = true) = 0;
	
	/**
	 * @brief Gets the Internet connection quality of audio.
	 * @param bSending true indicates to get the connection quality of sending the audio. false indicates to get the connection quality of receiving the audio.
	 * @return If the function succeeds, it returns one of those enumerated in ConnectionQuality enum. Otherwise, this function fails and returns nullptr.
	 * @note If you are not in the meeting, the Conn_Quality_Unknown will be returned.
	 */
	virtual ConnectionQuality GetAudioConnQuality(bool bSending = true) = 0;

	/**
	 * @brief Gets meeting audio statistics information.
	 * @param info_ [out] Audio statistics information.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError GetMeetingAudioStatisticInfo(MeetingAudioStatisticInfo& info) = 0;

	/**
	 * @brief Gets meeting video statistics information.
	 * @param info_ [out] Video statistics information.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
     */
	virtual SDKError GetMeetingVideoStatisticInfo(MeetingASVStatisticInfo& info) = 0;

	/**
	 * @brief Gets meeting share statistics information.
	 * @param info_ [out] Share statistics information.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
     */
	virtual SDKError GetMeetingShareStatisticInfo(MeetingASVStatisticInfo& info) = 0;
	
	/**
	 * @brief Gets video controller interface.
	 * @return If the function succeeds, the return value is a pointer to IMeetingVideoController. Otherwise returns nullptr.
	 */
	virtual IMeetingVideoController* GetMeetingVideoController() = 0;
	
	/**
	 * @brief Gets the sharing controller interface.
	 * @return If the function succeeds, the return value is a pointer to IMeetingVideoController. Otherwise returns nullptr.
	 */
	virtual IMeetingShareController* GetMeetingShareController() = 0;
	
	/**
	 * @brief Gets the audio controller interface.
	 * @return If the function succeeds, the return value is a pointer to IMeetingAudioController. Otherwise returns nullptr.
	 */
	virtual IMeetingAudioController* GetMeetingAudioController() = 0;
	
	/**
	 * @brief Gets the recording controller interface.
	 * @return If the function succeeds, the return value is a pointer to IMeetingRecordingController. Otherwise returns nullptr.
	 */
	virtual IMeetingRecordingController* GetMeetingRecordingController() = 0;
	
	/**
	 * @brief Gets the waiting room controller interface.
	 * @return If the function succeeds, the return value is a pointer to IMeetingWaitingRoomController. Otherwise returns nullptr.
	 */
	virtual IMeetingWaitingRoomController* GetMeetingWaitingRoomController() = 0;
	
	/**
	 * @brief Gets the participants controller interface.
	 * @return If the function succeeds, the return value is a pointer to IMeetingParticipantsController. Otherwise returns nullptr.
	 */
	virtual IMeetingParticipantsController* GetMeetingParticipantsController() = 0;
	
	/**
	 * @brief Gets the webinar controller interface.
	 * @return If the function succeeds, the return value is a pointer to IMeetingWebinarController. Otherwise returns nullptr.
	 */
	virtual IMeetingWebinarController* GetMeetingWebinarController() = 0;
	
	/**
	 * @brief Gets the Raw Archiving controller.
	 * @return If the function succeeds, the return value is a pointer to IMeetingRawArchivingController. Otherwise returns nullptr.
	 */
	virtual IMeetingRawArchivingController* GetMeetingRawArchivingController() = 0;
	
	/**
	 * @brief Gets the reminder controller.
	 * @return If the function succeeds, the return value is a pointer to IMeetingReminderController. Otherwise the function returns nullptr.
	 */
	virtual IMeetingReminderController* GetMeetingReminderController() = 0;
	
	/**
	 * @brief Gets the smart summary controller.
	 * @return If the function succeeds, the return value is a pointer to IMeetingSmartSummaryController. Otherwise the function returns nullptr.
	 * @deprecated This interface is marked as deprecated, and is replaced by GetMeetingSmartSummaryHelper() in class IMeetingAICompanionController.
	 */
	virtual IMeetingSmartSummaryController* GetMeetingSmartSummaryController() = 0;
	
	/**
	 * @brief Gets the chat controller interface.
	 * @return If the function succeeds, the return value is a pointer to IMeetingChatController. Otherwise returns nullptr.
	 */
	virtual IMeetingChatController* GetMeetingChatController() = 0;
	
	/**
	 * @brief Gets the Breakout Room controller.
	 * @return If the function succeeds, the return value is a pointer to IMeetingBOController. Otherwise returns nullptr.
	 */
	virtual IMeetingBOController* GetMeetingBOController() = 0;
	
	/**
	 * @brief Gets the meeting configuration interface.
	 * @return If the function succeeds, the return value is the meeting configuration interface. Otherwise returns nullptr.
	 */
	virtual IMeetingConfiguration* GetMeetingConfiguration() = 0;
	
	/**
	 * @brief Gets the AI companion controller.
	 * @return If the function succeeds, the return value is a pointer to IMeetingAICompanionController. Otherwise the function returns nullptr.
	 */
	virtual IMeetingAICompanionController* GetMeetingAICompanionController() = 0;
	
#if defined(WIN32)
	/**
	 * @brief Gets the meeting UI controller interface.
	 * @return If the function succeeds, the return value is a pointer to the IMeetingConfiguration. Otherwise returns nullptr.
	 */
	virtual IMeetingUIController* GetUIController() = 0;
	
	/**
	 * @brief Gets the annotation controller interface.
	 * @return If the function succeeds, the return value is a pointer of IAnnotationController. Otherwise returns nullptr.
	 */
	virtual IAnnotationController* GetAnnotationController() = 0;
	
	/**
	 * @brief Gets the remote controller interface.
	 * @return If the function succeeds, the return value is a pointer of IMeetingVideoController. Otherwise returns nullptr.
	 */
	virtual IMeetingRemoteController* GetMeetingRemoteController() = 0;
	
	/**
	 * @brief Gets the meeting H.323 helper interface.
	 * @return If the function succeeds, the return value is a pointer to IMeetingH323Helper. Otherwise returns nullptr.
	 */
	virtual IMeetingH323Helper* GetH323Helper() = 0;
	
	/**
	 * @brief Gets the meeting phone helper interface.
	 * @return If the function succeeds, the return value is a pointer of IMeetingPhoneHelper. Otherwise returns nullptr.
	 */
	virtual IMeetingPhoneHelper* GetMeetingPhoneHelper() = 0;
	
	/**
	 * @brief Gets the live stream controller interface.
	 * @return If the function succeeds, the return value is a pointer to IMeetingLiveStreamController. Otherwise returns nullptr.
	 */
	virtual IMeetingLiveStreamController* GetMeetingLiveStreamController() = 0;
	
	/**
	 * @brief Gets the Closed Caption controller interface.
	 * @return If the function succeeds, the return value is a pointer to IMeetingWebinarController. Otherwise returns nullptr.
	 */
	virtual IClosedCaptionController* GetMeetingClosedCaptionController() = 0;
	
	/**
	 * @brief Gets the real name auth controller interface.
	 * @return If the function succeeds, the return value is a pointer to IZoomRealNameAuthMeetingHelper. Otherwise returns nullptr.
	 */
	virtual IZoomRealNameAuthMeetingHelper* GetMeetingRealNameAuthController() = 0;
	
	/**
	 * @brief Gets the Q&A controller.
	 * @return If the function succeeds, the return value is a pointer to IMeetingQAController. Otherwise returns nullptr.
	 */
	virtual IMeetingQAController* GetMeetingQAController() = 0;
	
	/**
	 * @brief Gets the Interpretation controller.
	 * @return If the function succeeds, the return value is a pointer to IMeetingInterpretationController. Otherwise returns nullptr.
	 */
	virtual IMeetingInterpretationController* GetMeetingInterpretationController() = 0;
	
	/**
	 * @brief Gets the sign interpretation controller.
	 * @return If the function succeeds, the return value is a pointer to IMeetingSignInterpretationController. Otherwise returns nullptr.
	 */
	virtual IMeetingSignInterpretationController* GetMeetingSignInterpretationController() = 0;
	
	/**
	 * @brief Gets the Reaction controller.
	 * @return If the function succeeds, the return value is a pointer to IEmojiReactionController. Otherwise returns nullptr.
	 */
	virtual IEmojiReactionController* GetMeetingEmojiReactionController() = 0;
	
	/**
	 * @brief Gets the AAN controller.
	 * @return If the function succeeds, the return value is a pointer to IMeetingAANController. Otherwise returns nullptr.
	 */
	virtual IMeetingAANController* GetMeetingAANController() = 0;
	
	/**
	 * @brief Gets the immersive controller.
	 * @return If the function succeeds, the return value is a pointer to ICustomImmersiveController. Otherwise the function returns nullptr.
	 */
	virtual ICustomImmersiveController* GetMeetingImmersiveController() = 0;
	
	/**
	 * @brief Gets the Whiteboard controller.
	 * @return If the function succeeds, the return value is a pointer to IMeetingWhiteboardController. Otherwise the function returns nullptr.
	 */
	virtual IMeetingWhiteboardController* GetMeetingWhiteboardController() = 0;
	
	/**
	 * @brief Gets the Docs controller.
	 * @return If the function succeeds, the return value is a pointer to IMeetingDocsController. Otherwise the function returns nullptr.
	 */
	virtual IMeetingDocsController* GetMeetingDocsController() = 0;
	
	/**
	 * @brief Gets the Polling controller.
	 * @return If the function succeeds, the return value is a pointer to IMeetingPollingController. Otherwise the function returns nullptr.
	 */
	virtual IMeetingPollingController* GetMeetingPollingController() = 0;
	
	/**
	 * @brief Gets the remote support controller.
	 * @return If the function succeeds, the return value is a pointer to IMeetingRemoteSupportController. Otherwise the function returns nullptr.
	 */
	virtual IMeetingRemoteSupportController* GetMeetingRemoteSupportController() = 0;
	
	/**
	 * @brief Gets the Indicator controller.
	 * @return If the function succeeds, the return value is a pointer to IMeetingIndicatorController. Otherwise the function returns nullptr.
	 */
	virtual IMeetingIndicatorController* GetMeetingIndicatorController() = 0;
	
	/**
	 * @brief Gets the production studio controller.
	 * @return If the function succeeds, the return value is a pointer to IMeetingProductionStudioController. Otherwise returns nullptr.
	 */
	virtual IMeetingProductionStudioController* GetMeetingProductionStudioController() = 0;
#endif
	/**
	 * @brief Gets data center information
	 */
	virtual const zchar_t* GetInMeetingDataCenterInfo() = 0;
	
	/**
	 * @brief Gets the encryption controller.
	 * @return If the function succeeds, the return value is a pointer to IMeetingEncryptionController. Otherwise returns nullptr.
	 */
	virtual IMeetingEncryptionController* GetInMeetingEncryptionController() = 0;

	/**
	 * @brief Returns the list factory instance.
	 * @return IListFactory* A pointer to an IListFactory instance for handling list operations.Otherwise returns nullptr.
	 */
	 virtual IListFactory* GetListFactory() = 0;
};
END_ZOOM_SDK_NAMESPACE
#endif