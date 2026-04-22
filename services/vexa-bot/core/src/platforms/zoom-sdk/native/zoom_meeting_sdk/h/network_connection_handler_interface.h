/**
 * @file network_connection_handler_interface.h
 * @brief Network Connection Message Notification Handler.
 */

#ifndef _NETWORK_CONNECTION_HANDLER_H_
#define _NETWORK_CONNECTION_HANDLER_H_

BEGIN_ZOOM_SDK_NAMESPACE

/**
 * @brief The proxy that the user want to use according to the net environment.
 * Here are more detailed structural descriptions.
 */
typedef struct tagProxySettings
{
	/** The proxy ip address and port that user want to use, should be organized like ip:port, such as '255.255.255.255:8080'. Use ';' to separate multi proxies. */
	const zchar_t* proxy;
	/** true indicates automatic detection. */
	bool auto_detect;
	tagProxySettings()
	{
		proxy = nullptr;
		auto_detect = false;
	}
}ProxySettings;

/**
 * @class IProxySettingHandler
 * @brief Proxy setting information callback interface.
 */
class IProxySettingHandler
{
public:
	virtual ~IProxySettingHandler(){};
	
	/**
	 * @brief Gets the address of the proxy host.
	 * @return the address of the proxy host.
	 */
	virtual const zchar_t* GetProxyHost() = 0;
	
	/**
	 * @brief Gets the proxy port.
	 * @return The value of the proxy port.
	 */
	virtual unsigned int GetProxyPort() = 0;
	
	/**
	 * @brief Gets the description of the proxy.
	 * @return The description of the proxy.
	 */
	virtual const zchar_t* GetProxyDescription() = 0;
	
	/**
	 * @brief Input the username and password when using the proxy.
	 * @param userName The username when using the proxy.
	 * @param psw The password when using the proxy.
	 */
	virtual void InputUsernamePassword(const zchar_t* userName, const zchar_t* psw) = 0;
	
	/**
	 * @brief Cancel the process to input the username and password of the proxy.
	 */
	virtual void Cancel() = 0;

};

/**
 * @class ISSLCertVerificationHandler
 * @brief Verification of the SSL certificate callback interface.
 */
class ISSLCertVerificationHandler
{
public:
	virtual ~ISSLCertVerificationHandler(){};
	
	/**
	 * @brief Gets the value of whom the SSL certificate is issued to.
	 */
	virtual const zchar_t* GetCertIssuedTo() = 0;
	
	/**
	 * @brief Gets the value that who issues the SSL certificate.
	 */
	virtual const zchar_t* GetCertIssuedBy() = 0;
	
	/**
	 * @brief Gets the serial number of the SSL certificate.
	 */
	virtual const zchar_t* GetCertSerialNum() = 0;
	
	/**
	 * @brief Gets the SSL certificate's fingerprint
	 */
	virtual const zchar_t* GetCertFingerprint() = 0;
	
	/**
	 * @brief The SSL certificate is trusted.
	 */
	virtual void Trust() = 0;
	
	/**
	 * @brief The SSL certificate is not trusted.
	 */
	virtual void Cancel() = 0;

};

/**
 * @class INetworkConnectionHandler
 * @brief The network connection handler callback event.
 */
class INetworkConnectionHandler
{
public:
	/**
	 * @brief Notification callback of completing the proxy detection.
	 */
	virtual void onProxyDetectComplete() = 0;
	
	/**
	 * @brief The callback will be triggered if the proxy requests to input the username and password.
	 * @note Use the handler to configure the related information.
	 * The handler will be destroyed once the function calls end.
	 */
	virtual void onProxySettingNotification(IProxySettingHandler* handler) = 0;
	
	/**
	 * @brief The callback will be triggered when the SSL is verified.
	 * @note Use the handler to check the related information.
	 * The handler will be destroyed once the function calls end.
	 */
	virtual void onSSLCertVerifyNotification(ISSLCertVerificationHandler* handler) = 0;
};

/**
 * @class INetworkConnectionHelper
 * @brief The network connection helper interface.
 */
class INetworkConnectionHelper
{
public:
	/**
	 * @brief Sets the callback handler to receive the INetworkConnectionHandler.
	 * @param pNetworkHandler The SDK will invoke the handler once received this type of message.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError RegisterNetworkConnectionHandler(INetworkConnectionHandler* pNetworkHandler) = 0;
	
	/**
	 * @brief Unregister the callback handler which is used to receive the INetworkConnectionHandler.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError UnRegisterNetworkConnectionHandler() = 0;
	
	/**
	 * @brief Sets the proxy that the user want to use according to your net environment.
	 * @param proxy_setting The proxy ip address and port that user want to use, should be organized like ip:port, such as '255.255.255.255:8080'. Using ';' to separate multi proxies.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	virtual SDKError ConfigureProxy(ProxySettings& proxy_setting) = 0;
};
END_ZOOM_SDK_NAMESPACE
#endif