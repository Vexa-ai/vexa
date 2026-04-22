/**
 * @file zoom_sdk_raw_data_def.h
 * @brief ZOOM SDK Raw Data Common Definition File.
 */
#ifndef _ZOOM_SDK_RAW_DATA_DEF_H_
#define _ZOOM_SDK_RAW_DATA_DEF_H_
/**
 * @brief The audio raw data handler interface.
 */
class AudioRawData
{
public:
	/**
	 * @brief Determine if the reference count can be increased.
	 * @return TRUE indicates to the reference count can be increased.
	 */
	virtual bool CanAddRef() = 0;
	
	/**
	 * @brief Add one to the reference count.
	 * @return If the function succeeds, the return value is TRUE.
	 */
	virtual bool AddRef() = 0;
	
	/**
	 * @brief Subtract one from the reference count.
	 * @return The current reference count. If the currrent reference count is 0, the SDK will delete this object instance.
	 */
	virtual int Release() = 0;
	
	/**
	 * @brief Get the audio raw data.
	 * @return A pointer to the audio raw data.
	 */
	virtual char* GetBuffer() = 0;
	
	/**
	 * @brief Get the buffer length of the audio raw data.
	 * @return The length of the audio raw data.
	 */
	virtual unsigned int GetBufferLen() = 0;
	
	/**
	 * @brief Get the sample rate of the audio raw data.
	 * @return The sample rate of the audio raw data.
	 */
	virtual unsigned int GetSampleRate() = 0;
	
	/**
	 * @brief Get the channel number of the audio raw data.
	 * @return The channel number of the audio raw data.
	 */
	virtual unsigned int GetChannelNum() = 0;
	
	/**
	 * @brief Get the timestamp of the raw data.
	 * @return Millisecond timestamp.
	 */
	virtual unsigned long long GetTimeStamp() = 0;

	virtual ~AudioRawData(){}
};

/**
 * @brief The YUV raw data handler interface.
 */
class YUVRawDataI420
{
public:
	/**
	 * @brief Determine if the reference count can be increased.
	 * @return TRUE indicates to the reference count can be increased.
	 */
	virtual bool CanAddRef() = 0;
	
	/**
	 * @brief Add one to the reference count.
	 * @return If the function succeeds, the return value is TRUE.
	 * @note
	 *  After increasing the reference count, the current object will not be automatically destroyed by SDK and you can use this object in other context.
	 *  When you no longer use this object, please call 'Release()', otherwise there will be a memory leak.
	 */
	virtual bool AddRef() = 0;
	
	/**
	 * @brief Subtract one from the reference count.
	 * @return The current reference count. If the current reference count is 0, the SDK will delete this object instance.
	 */
	virtual int Release() = 0;
	
	/**
	 * @brief Get the starting address of Y raw data.
	 * @return A pointer to the Y raw data.
	 */
	virtual char* GetYBuffer() = 0;
	
	/**
	 * @brief Get the starting address of U raw data.
	 * @return A pointer to the U raw data.
	 */
	virtual char* GetUBuffer() = 0;
	
	/**
	 * @brief Get the starting address of V raw data.
	 * @return A pointer to the V raw data.
	 */
	virtual char* GetVBuffer() = 0;
	
	/**
	 * @brief Get the alpha mask data of YUV raw data.
	 * @return A pointer to the alpha mask data.
	 */
	virtual char* GetAlphaBuffer() = 0;
	
	/**
	 * @brief Get the starting address of YUV raw data.
	 * @return A pointer to the YUV raw data.
	 */

	virtual char* GetBuffer() = 0;
	
	/**
	 * @brief Get the buffer length of the YUV raw data.
	 * @return The length of the YUV raw data.
	 */
	virtual unsigned int GetBufferLen() = 0;
	
	/**
	 * @brief Get the alpha mask data length.
	 * @return The length of alpha mask data.
	 */
	virtual unsigned int GetAlphaBufferLen() = 0;
	
	/**
	 * @brief Determine if the YUV raw data is limited range mode.
	 * @return True indicates to the YUV raw data is limited range mode.
	 */
	virtual bool IsLimitedI420() = 0;
	
	/**
	 * @brief Get the stream width.
	 * @return The stream width.
	 */
	virtual unsigned int GetStreamWidth() =0;
	
	/**
	 * @brief Get the stream height.
	 * @return The stream height.
	 */
	virtual unsigned int GetStreamHeight() = 0;
	
	/**
	 * @brief Get the rotation angle of the local video device.
	 * @return If the function succeeds, the return is enumerated in LocalVideoDeviceRotation enum 
	 */
	virtual unsigned int GetRotation() = 0;
	
	/**
	 * @brief Get the source_id of the current YUV raw data.
	 * @return The source_id.
	 */
	virtual unsigned int GetSourceID() = 0;
	
	/**
	 * @brief Get the timestamp of the raw data.
	 * @return Millisecond timestamp.
	 */
	virtual unsigned long long GetTimeStamp() = 0;

	virtual ~YUVRawDataI420(){}
};

class YUVProcessDataI420
{
public:
	virtual unsigned int GetWidth() =0;
	virtual unsigned int GetHeight() = 0;

	virtual char* GetYBuffer(unsigned int lineNum = 0) = 0;
	virtual char* GetUBuffer(unsigned int lineNum = 0) = 0;
	virtual char* GetVBuffer(unsigned int lineNum = 0) = 0;

	virtual unsigned int GetYStride() = 0;
	virtual unsigned int GetUStride() = 0;
	virtual unsigned int GetVStride() = 0;

	virtual unsigned int GetRotation() = 0;
	virtual bool IsLimitedI420() = 0;
	virtual ~YUVProcessDataI420(){}
};

/**
 * @brief I420 YUV raw data converter interface.
 */
class IYUVRawDataI420Converter
{
public:
	/**
	 * @brief Convert the intermediate YUV raw data to YUV raw data.
	 * @return The YUV raw data. A pointer to YUVRawDataI420.
	 */
	virtual YUVRawDataI420* ConvertToYUV() = 0;
	
	/**
	 * @brief Convert the intermediate YUV raw data to YUV raw data via external buffer.
	 * @param buffer Specifies the external buffer.
	 * @param size_ Specifies the size of the YUV raw data.
	 * @return The YUV raw data. A pointer to YUVRawDataI420.
	 */
	virtual YUVRawDataI420* ConvertToYUVViaExternalBuffer(char* buffer_, int size_) = 0;
	
	/**
	 * @brief Fill data to the pixel buffer if expanding the size of the intermediate YUV raw data.
	 * @param ybuffer_ Specifies the y-data of intermediate YUV raw data.
	 * @param ybuffer_pre_row_bytes Specifies the width of the y-data.
	 * @param uvbuffer_ Specifies the uv-data of intermediate YUV raw data.
	 * @param uvbuffer_pre_row_bytes Specifies the width of the uv-data.
	 * @param width Specifies the new width to extended.
	 * @param height Specifies the new height to extended.
	 */
	virtual void FillToPixelBuffer(char* ybuffer_, int ybuffer_pre_row_bytes, char* uvbuffer_, int uvbuffer_pre_row_bytes,int width, int height) = 0;

	virtual ~IYUVRawDataI420Converter(){}
};

#endif