/**
 * @file meeting_inmeeting_encryption_interface.h
 * @brief Meeting Service Encryption Interface
 * @note Valid for both ZOOM style and user custom interface mode.
 */

#ifndef _MEETING_INMEETING_ENCRYPTION_INTERFACE_H_
#define _MEETING_INMEETING_ENCRYPTION_INTERFACE_H_
#include "zoom_sdk_def.h"

BEGIN_ZOOM_SDK_NAMESPACE

class IMeetingEncryptionControllerEvent
{
public:
	virtual ~IMeetingEncryptionControllerEvent() {}
	
	/**
	 * @brief This callback will be called when the security code changes
	 */
	virtual void onE2EEMeetingSecurityCodeChanged() = 0;
};

/**
 * @brief Enumeration of encryption type.
 */
enum EncryptionType
{
	/** For initialization. */
	EncryptionType_None,
	/** Meeting encryption type is Enhanced. */	
	EncryptionType_Enhanced, 
	/** Meeting encryption type is E2EE. */	
	EncryptionType_E2EE       
}; 

class IMeetingEncryptionController
{
public:
	virtual ~IMeetingEncryptionController() {}
	
	/**
	 * @brief Sets the encryption controller callback handler.
	 * @param pEvent A pointer to the IMeetingEncryptionControllerEvent that receives the encryption event. 
	 */
	virtual void SetEvent(IMeetingEncryptionControllerEvent* pEvent) = 0;
	
	/**
	 * @brief Gets meeting encryption type.
	 * @return The encryption type.
	 */
	virtual EncryptionType GetEncryptionType() = 0;
	
	/**
	 * @brief Get E2EE meeting security code.
	 * @return The security code.
	 */
	virtual const zchar_t* GetE2EEMeetingSecurityCode() = 0;
	
	/**
	 * @brief Gets security code passed seconds.
	 * @return Time in seconds.
	 */
	virtual unsigned int GetE2EEMeetingSecurityCodePassedSeconds() = 0;
	
	/**
	 * @brief Determines whether unencrypted exception data is valid.
	 * @return true if it's valid. Otherwise, false.
	 */
	virtual bool IsUnencryptedExceptionDataValid() = 0;
	
	/**
	 * @brief Gets unencrypted exception count.
	 * @return Exception count.
	 */
	virtual unsigned int GetUnencryptedExceptionCount() = 0;
	
	/**
	 * @brief Gets unencrypted exception details.
	 * @return Exception details.
	 */
	virtual const zchar_t* GetUnencryptedExceptionInfo() = 0;
};

END_ZOOM_SDK_NAMESPACE
#endif