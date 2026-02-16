/**
 * @file auth_service_interface.h
 * @brief Authentication Service Interface 
 */
#ifndef _AUTH_SERVICE_INTERFACE_H_
#define _AUTH_SERVICE_INTERFACE_H_
#include "zoom_sdk_def.h"

BEGIN_ZOOM_SDK_NAMESPACE

/**
 * @brief Enumeration of SDK authentication result.
 */
enum AuthResult
{ 
	/** Authentication is successful. */
	AUTHRET_SUCCESS,
	/**  The key or secret to authenticate is empty. */
	AUTHRET_KEYORSECRETEMPTY,
	/** he key or secret to authenticate is wrong */
	AUTHRET_KEYORSECRETWRONG,
	/** The user account does not support. */
	AUTHRET_ACCOUNTNOTSUPPORT,
	/** The user account is not enabled for SDK. */
	AUTHRET_ACCOUNTNOTENABLESDK,
	/** Unknown error. */
	AUTHRET_UNKNOWN,
	/** Service is busy. */
	AUTHRET_SERVICE_BUSY,
	/** Initial status. */
	AUTHRET_NONE,
	/** Time out. */
	AUTHRET_OVERTIME,
	/** Network issues. */
	AUTHRET_NETWORKISSUE,
	/** Account does not support this SDK version. */
	AUTHRET_CLIENT_INCOMPATIBLE, 
	/** The jwt token to authenticate is wrong. */
	AUTHRET_JWTTOKENWRONG, 
	/** The authentication rate limit is exceeded. */
	AUTHRET_LIMIT_EXCEEDED_EXCEPTION, 
};

/**
 * @brief Enumeration of SDK login status.
 */
enum LOGINSTATUS
{
	/** Unlogged in. */
	LOGIN_IDLE,
	/** In process of login. */
	LOGIN_PROCESSING,
	/** Login successful. */
	LOGIN_SUCCESS,
	/** Login failed. */
	LOGIN_FAILED,
};

/**
 * @brief Enumeration of SDK login failed reason.
 */
enum LoginFailReason
{
	/** No failure, login successful. */
	LoginFail_None = 0,
	/** Email login is disabled. */
	LoginFail_EmailLoginDisable,
	/** User does not exist. */
	LoginFail_UserNotExist,	
	/** Incorrect password. */
	LoginFail_WrongPassword,	
	/** Account is locked. */
	LoginFail_AccountLocked,	
	/** SDK needs to be updated. */
	LoginFail_SDKNeedUpdate,	
	/** Too many failed login attempts. */
	LoginFail_TooManyFailedAttempts,
	/** Incorrect SMS verification code. */
	LoginFail_SMSCodeError,	
	/** SMS verification code has expired. */
	LoginFail_SMSCodeExpired,	
	/** Invalid phone number format. */
	LoginFail_PhoneNumberFormatInValid,
	/** Invalid login token. */
	LoginFail_LoginTokenInvalid,
	/** User disagreed with the login disclaimer. */
	LoginFail_UserDisagreeLoginDisclaimer,
	/** Multi-factor authentication is required. */
	LoginFail_Mfa_Required,
	/** User needs to provide birthday. */
	LoginFail_Need_Bitrthday_ask,
	/** Other unspecified issue. */
	LoginFail_OtherIssue = 100, 
};
#if defined(WIN32)
/**
 * @brief Enumeration of the type for notification service status.
 */
typedef enum
{
	/** No service status. */
	SDK_Notification_Service_None = 0,
	/** Notification service is starting. */
	SDK_Notification_Service_Starting,
	/** Notification service has started successfully. */
	SDK_Notification_Service_Started,	
	/** Failed to start the notification service. */
	SDK_Notification_Service_StartFailed,
	/** Notification service has been closed. */
	SDK_Notification_Service_Closed,
}SDKNotificationServiceStatus;

/**
 * @brief Enumeration of notification service error codes.
 */
typedef enum
{
	/** Operation completed successfully. */
	SDK_Notification_Service_Error_Success = 0,
	/** An unknown error occurred. */
	SDK_Notification_Service_Error_Unknown,
	/** An internal error occurred. Retry the operation. */
	SDK_Notification_Service_Error_Internal_Error,
	/** The provided token is invalid. */
	SDK_Notification_Service_Error_Invalid_Token,
	/**
	 * @brief Multiple connections detected.
	 *
	 * This happens when the same user or resource logs in again on the same device.
	 * The previous login application will receive this error.
	 */
	SDK_Notification_Service_Error_Multi_Connect,
	/** A network issue prevented the operation from succeeding. */
	SDK_Notification_Service_Error_Network_Issue,
	/**
	 * @brief The client has been connected for too long (over 24 hours).
	 *
	 * The server will disconnect the session. The client must reconnect or log in again.
	 */
	SDK_Notification_Service_Error_Max_Duration, 
}SDKNotificationServiceError;

#endif

/**
 * @brief SDK authentication parameter with jwt token.
 */
typedef struct tagAuthContext
{
	/**
	 * @brief JWT token. You may generate your JWT token using the online tool https://jwt.io/. 
	 * @note It is highly recommended to generate your JWT token in your backend server.
	 * JWT is generated with three core parts: Header, Payload, and Signature. When combined, these parts are separated by a period to form a token: `aaaaa.bbbbb.cccc`.
     * Please follow this template to compose your payload for SDK initialization:
	 *		Header
	 *		{
	 *		"alg": "HS256",
	 *		"typ": "JWT"
	 *		}
	 *		Payload
	 *		{
	 *		"appKey": "string", // Your SDK key
	 *		"iat": long, // access token issue timestamp
	 *		"exp": long, // access token expire time
	 *		"tokenExp": long // token expire time
	 *		}
	 *		Signature
	 *		HMACSHA256(
	 *			base64UrlEncode(header) + "." +
	 *			base64UrlEncode(payload),
	 *			"Your SDK secret here"
		)
	 */
	const zchar_t* jwt_token; 
	tagAuthContext()
	{
		jwt_token = nullptr;
	}

}AuthContext;

/**
 * @brief Enumeration of user login type.
 */
enum LoginType
{
	/** Unknown login type. */
	LoginType_Unknown,
	/** Login with SSO token. */
	LoginType_SSO,
};

/**
 * @class IAccountInfo
 * @brief Account information interface.
 */
class IAccountInfo
{
public:
    /**
	 * @brief Gets the screen name of user.
	 * @return If the function succeeds, it returns the displayed username. If there is no screen name of user, this function returns a string of length ZERO(0).
	 */
	virtual const zchar_t* GetDisplayName() = 0;
    /**
	 * @brief Gets login type.
	 * @return If the function succeeds, it returns the account login type.
     */
	virtual LoginType GetLoginType() = 0;
	virtual ~IAccountInfo(){};
};

/**
 * @class IAuthServiceEvent
 * @brief Authentication service callback event.
 */
class IAuthServiceEvent
{
public:
	virtual ~IAuthServiceEvent() {}

    /**
	 * @brief Notification of authentication result.
	 * @param ret Authentication result value.
	 */
	virtual void onAuthenticationReturn(AuthResult ret) = 0;

    /**
	 * @brief Notification of login result with fail reason.
	 * @param ret Login status.
	 * @param pAccountInfo Valid when the ret is LOGINRET_SUCCESS. Otherwise nullptr.
	 * @param reason Login fail reason. Valid when the ret is LOGIN_FAILED. Otherwise LoginFail_None.
	 */
	virtual void onLoginReturnWithReason(LOGINSTATUS ret, IAccountInfo* pAccountInfo, LoginFailReason reason) = 0;
	
    /**
	 * @brief Notification of logout.
	 */
	virtual void onLogout() = 0;

    /**
	 * @brief Notification of Zoom identity has expired, please re-login or generate a new zoom access token via REST Api.
	 */
	virtual void onZoomIdentityExpired() = 0;

    /**
	 * @brief Notification of Zoom authentication identity will be expired in 10 minutes, please re-auth.
	 */
	virtual void onZoomAuthIdentityExpired() = 0;
#if defined(WIN32)
    /**
	 * @brief Notification of service status changed.
	 * @param status The value of transfer meeting service.
	 * @param error Connection Notification service fail error code.
	 */
	virtual void onNotificationServiceStatus(SDKNotificationServiceStatus status, SDKNotificationServiceError error) = 0;
#endif
};
#if defined(WIN32)
class IDirectShareServiceHelper;
class IOutlookPluginIntegrationHelper;
class INotificationServiceHelper;
#endif
/**
 * @class IAuthService
 * @brief Authentication Service Interface.
 */
class IAuthService
{
public:
    /**
	 * @brief Sets the authentication service callback event handler.
	 * @param pEvent A pointer to receive authentication event. 
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError SetEvent(IAuthServiceEvent* pEvent) = 0;

    /**
	 * @brief Authenticates SDK with JWT token.
	 * @param authContext The parameter to be used for authentication SDK.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError SDKAuth(AuthContext& authContext) = 0;

     /**
	 * @brief Gets authentication status.
	 * @return If the function succeeds, it returns the authentication status.
	 */
	virtual AuthResult GetAuthResult() = 0;

    /**
	 * @brief Gets SDK identity.
	 * @return If the function succeeds, it returns the SDK identity. Otherwise, this function fails and returns nullptr.
	 */
	virtual const zchar_t* GetSDKIdentity() = 0;

    /**
	 * @brief Gets SSO login web URL.
	 * @param prefix_of_vanity_url The prefix of vanity URL. 
	 * @return If the function succeeds, it returns the SSO login web URL. Otherwise, this function fails and returns nullptr.
	 */
	virtual const zchar_t* GenerateSSOLoginWebURL(const zchar_t* prefix_of_vanity_url) = 0;

    /**
	 * @brief Performs account login.
	 * @param uri_protocol The parameter to be used for SSO account login.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 * @note You need to call this API after IAuthServiceEvent::onAuthenticationReturn(AUTHRET_SUCCESS).
	 */
	virtual SDKError SSOLoginWithWebUriProtocol(const zchar_t* uri_protocol) = 0;
	
    /**
	 * @brief Performs account logout.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError LogOut() = 0;

    /**
	 * @brief Gets login account information.
	 * @return If you have logged in your account successfully, the return value is a pointer to IAccountInfo. Otherwise, this function returns nullptr.
	 */
	virtual IAccountInfo* GetAccountInfo() = 0;

    /**
	 * @brief Gets login status.
	 * @return If the function succeeds, it returns the login status. Otherwise, this function returns an error.
	 */
	virtual LOGINSTATUS GetLoginStatus() = 0;
#if defined(WIN32)
    /**
	 * @brief Gets direct share service helper interface. 
	 * @return If you have logged in your account successfully, the return value is a pointer to IDirectShareServiceHelper. Otherwise, this function returns nullptr.
	 */
	virtual IDirectShareServiceHelper* GetDirectShareServiceHelper() = 0;

    /**
	 * @brief Enables or disables auto register notification service. This is enabled by default.
	 * @param bEnable true to enable, false to disable. 
	 */
	virtual void EnableAutoRegisterNotificationServiceForLogin(bool bEnable) = 0;

    /**
	 * @brief Registers notification service.
	 * @param accessToken The initialization parameter of notification service.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError RegisterNotificationService(const zchar_t* accessToken) = 0;

    /**
	 * @brief Unregisters notification service.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError UnregisterNotificationService() = 0;

    /**
	 * @brief Gets notification service helper interface. 
	 * @return If the function succeeds, the return value is a pointer to INotificationServiceHelper. Otherwise, this function fails and returns nullptr.
	 */
	virtual INotificationServiceHelper* GetNotificationServiceHelper() = 0;
#endif
};
END_ZOOM_SDK_NAMESPACE
#endif
