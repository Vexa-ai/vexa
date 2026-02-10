#ifndef _ZOOM_SDK_PLATFORM_H_
#define _ZOOM_SDK_PLATFORM_H_
#if defined ( _WIN32 ) || defined ( __WIN32__ ) || defined ( WIN32 )
#ifdef ZOOMSDK_EXPORT
#define ZOOMSDK_MODULE_API __declspec(dllexport) 
#else
#define ZOOMSDK_MODULE_API
#endif
#elif defined ( macintosh ) || defined ( __APPLE__ ) || defined ( __APPLE_CC__ ) || defined (ANDROID)
#ifdef ZOOMSDK_EXPORT
#define ZOOMSDK_MODULE_API __attribute__((visibility("default"))) 
#else
#define ZOOMSDK_MODULE_API
#endif
#else 
#define ZOOMSDK_MODULE_API
#endif

#define ZOOM_RAWDATA_NAMESPACE ZOOMSDKRAWDATA
#define BEGIN_ZOOM_RAWDATA_NAMESPACE namespace ZOOM_RAWDATA_NAMESPACE {
#define END_ZOOM_RAWDATA_NAMESPACE };
#define USING_ZOOM_RAWDATA_NAMESPACE using namespace ZOOM_RAWDATA_NAMESPACE;

#if defined ( _WIN32 ) || defined ( __WIN32__ ) || defined ( WIN32 )
#include <tchar.h>
#define zTCHAR TCHAR
#else
#define zTCHAR  char
#endif

template<class T>
class IVector
{
public:
	virtual ~IVector() {};
	virtual int GetCount() = 0;
	virtual T   GetItem(int index) = 0;
};
#endif
