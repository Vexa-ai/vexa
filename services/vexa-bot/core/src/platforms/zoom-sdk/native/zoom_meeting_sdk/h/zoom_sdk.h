/**
 * @file zoom_sdk.h
 * @brief ZOOM SDK. 
 */

#ifndef _ZOOM_SDK_H_
#define _ZOOM_SDK_H_
#include "zoom_sdk_def.h"

BEGIN_ZOOM_SDK_NAMESPACE
extern "C"
{
	class IMeetingService;
	class IAuthService;
	class ISettingService;
	class ICalenderService;
	class INetworkConnectionHelper;
	
	/**
	 * @brief Initializes ZOOM SDK.
	 * @param initParam The initialization parameter for ZOOM SDK.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	SDK_API SDKError InitSDK(InitParam& initParam);

	/**
	 * @brief Switches ZOOM SDK domain.
	 * @param new_domain The new domain to switch to.
	 * @param bForce true to force the domain switch, false otherwise.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	SDK_API SDKError SwitchDomain(const zchar_t* new_domain, bool bForce);
	
	/**
	 * @brief Creates meeting service interface.
	 * @param ppMeetingService An object pointer to the IMeetingService*. 
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS while the value of ppMeetingService is not nullptr. Otherwise, this function returns an error.
	 */
	SDK_API SDKError CreateMeetingService(IMeetingService** ppMeetingService);
	
	/**
	 * @brief Destroys the specified meeting service interface.
	 * @param pMeetingService A pointer to the IMeetingService to be destroyed. 
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	SDK_API SDKError DestroyMeetingService(IMeetingService* pMeetingService);
	
	/**
	 * @brief Creates authentication service interface.
	 * @param ppAuthService An object pointer to the IAuthService*. 
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS while the value of ppAuthService is not nullptr. Otherwise, this function returns an error.
	 */
	SDK_API SDKError CreateAuthService(IAuthService** ppAuthService);
	
	/**
	 * @brief Destroys the specified authentication service interface.
	 * @param pAuthService A pointer to the IAuthService to be destroyed. 
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	SDK_API SDKError DestroyAuthService(IAuthService* pAuthService);
	
	/**
	 * @brief Creates setting service interface.
	 * @param ppSettingService An object pointer to the ISettingService*. 
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS while the value of ppSettingService is not nullptr. Otherwise, this function returns an error.
	 */
	SDK_API SDKError CreateSettingService(ISettingService** ppSettingService);
	
	/**
	 * @brief Destroys the specified setting service interface.
	 * @param pSettingService A pointer to the ISettingService to be destroyed. 
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	SDK_API SDKError DestroySettingService(ISettingService* pSettingService);
	
	/**
	 * @brief Creates network connection helper interface.
	 * @param ppNetworkHelper An object pointer to the INetworkConnectionHelper*. 
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS while the value of ppNetworkHelper is not nullptr. Otherwise, this function returns an error.
	 */
	SDK_API SDKError CreateNetworkConnectionHelper(INetworkConnectionHelper** ppNetworkHelper);
	
	/**
	 * @brief Destroys the specified network connection helper interface.
	 * @param pNetworkHelper A pointer to the INetworkConnectionHelper to be destroyed. 
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 */
	SDK_API SDKError DestroyNetworkConnectionHelper(INetworkConnectionHelper* pNetworkHelper);
	
	/**
	 * @brief Cleans up ZOOM SDK.
	 * @return If the function succeeds, the return value is SDKERR_SUCCESS. Otherwise, this function returns an error.
	 * @note This function must not be called within any SDK callback. Calling CleanUPSDK() inside a callback may cause unexpected behavior.
	 */
	SDK_API SDKError CleanUPSDK();
	
	/**
	 * @brief Gets the version of ZOOM SDK.
	 * @return If the function succeeds, it returns the version of ZOOM SDK. 
	 */
	SDK_API const zchar_t* GetSDKVersion();
	
	/**
	 * @brief Gets ZOOM last error interface.
	 * @return If the function succeeds, it returns an interface of ZOOM last error. If the function fails or there is no error, this function returns nullptr.
	 */
	SDK_API const IZoomLastError* GetZoomLastError();
}

END_ZOOM_SDK_NAMESPACE
#endif