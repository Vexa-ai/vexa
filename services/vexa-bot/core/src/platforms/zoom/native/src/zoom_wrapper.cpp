/**
 * Zoom Meeting SDK N-API wrapper
 * Uses libmeetingsdk.so (Linux x86_64)
 */

#include <napi.h>
#include <iostream>
#include <string>
#include <vector>
#include <stdint.h>
#include <cstring>

// Qt event loop (required for Zoom SDK async callbacks on Linux)
#include <QCoreApplication>
#include <QEventLoop>

// libuv (to hook into Node.js event loop for Qt event pumping)
#include <uv.h>

// Zoom SDK headers
#include "zoom_sdk.h"
#include "auth_service_interface.h"
#include "meeting_service_interface.h"
#include "zoom_sdk_def.h"
#include "zoom_sdk_raw_data_def.h"
#include "rawdata/zoom_rawdata_api.h"
#include "rawdata/rawdata_audio_helper_interface.h"
#include "meeting_service_components/meeting_audio_interface.h"
#include "meeting_service_components/meeting_participants_ctrl_interface.h"

// Global Qt application (runs on Node.js main thread)
static QCoreApplication* g_qtApp = nullptr;
static uv_idle_t g_uvIdle;
static bool g_idleStarted = false;

// Called by libuv on each event loop iteration - pumps Qt events
static void pumpQtEvents(uv_idle_t* /*handle*/) {
    if (g_qtApp) {
        g_qtApp->processEvents(QEventLoop::AllEvents, 5);
    }
}

static void ensureQtApp(Napi::Env /*env*/) {
    if (g_qtApp) return;
    static int argc = 0;
    g_qtApp = new QCoreApplication(argc, nullptr);
    // Register libuv idle handler to pump Qt events on every Node.js tick.
    // uv_default_loop() is the same loop Node.js uses on the main thread.
    uv_loop_t* loop = uv_default_loop();
    if (loop && !g_idleStarted) {
        uv_idle_init(loop, &g_uvIdle);
        uv_idle_start(&g_uvIdle, pumpQtEvents);
        // Unref so the idle handle doesn't prevent process exit
        uv_unref((uv_handle_t*)&g_uvIdle);
        g_idleStarted = true;
    }
    std::cout << "[ZoomSDK] Qt event pumping via libuv idle registered" << std::endl;
}

using namespace ZOOMSDK;

// ============================================================
// Auth Event Handler
// ============================================================
class AuthEventHandler : public IAuthServiceEvent {
public:
    Napi::ThreadSafeFunction tsf_;

    void onAuthenticationReturn(AuthResult ret) override {
        if (!tsf_) return;
        int code = (int)ret;
        bool success = (ret == AUTHRET_SUCCESS);
        tsf_.NonBlockingCall([code, success](Napi::Env env, Napi::Function jsCallback) {
            Napi::Object result = Napi::Object::New(env);
            result.Set("success", Napi::Boolean::New(env, success));
            result.Set("code", Napi::Number::New(env, code));
            jsCallback.Call({result});
        });
    }

    void onLoginReturnWithReason(LOGINSTATUS, IAccountInfo*, LoginFailReason) override {}
    void onLogout() override {}
    void onZoomIdentityExpired() override {}
    void onZoomAuthIdentityExpired() override {}
};

// ============================================================
// Meeting Event Handler
// ============================================================
class MeetingEventHandler : public IMeetingServiceEvent {
public:
    Napi::ThreadSafeFunction tsf_;

    void onMeetingStatusChanged(MeetingStatus status, int iResult) override {
        if (!tsf_) return;
        int statusInt = (int)status;
        int code = iResult;
        tsf_.NonBlockingCall([statusInt, code](Napi::Env env, Napi::Function jsCallback) {
            Napi::Object result = Napi::Object::New(env);
            const char* statusStr = "unknown";
            switch ((MeetingStatus)statusInt) {
                case MEETING_STATUS_IDLE:             statusStr = "idle"; break;
                case MEETING_STATUS_CONNECTING:       statusStr = "connecting"; break;
                case MEETING_STATUS_WAITINGFORHOST:   statusStr = "waiting_for_host"; break;
                case MEETING_STATUS_INMEETING:        statusStr = "in_meeting"; break;
                case MEETING_STATUS_DISCONNECTING:    statusStr = "disconnecting"; break;
                case MEETING_STATUS_RECONNECTING:     statusStr = "reconnecting"; break;
                case MEETING_STATUS_FAILED:           statusStr = "failed"; break;
                case MEETING_STATUS_ENDED:            statusStr = "ended"; break;
                case MEETING_STATUS_IN_WAITING_ROOM:  statusStr = "waiting_room"; break;
                default: statusStr = "unknown"; break;
            }
            result.Set("status", Napi::String::New(env, statusStr));
            result.Set("code", Napi::Number::New(env, code));
            jsCallback.Call({result});
        });
    }

    void onMeetingStatisticsWarningNotification(StatisticsWarningType) override {}
    void onMeetingParameterNotification(const MeetingParameter*) override {}
    void onSuspendParticipantsActivities() override {}
    void onAICompanionActiveChangeNotice(bool) override {}
    void onMeetingTopicChanged(const zchar_t*) override {}
    void onMeetingFullToWatchLiveStream(const zchar_t*) override {}
    void onUserNetworkStatusChanged(MeetingComponentType, ConnectionQuality, unsigned int, bool) override {}
};

// ============================================================
// Audio Event Handler (for speaker detection)
// ============================================================
class AudioEventHandler : public IMeetingAudioCtrlEvent {
public:
    Napi::ThreadSafeFunction tsfSpeaker_;

    void onUserActiveAudioChange(IList<unsigned int>* plstActiveAudio) override {
        if (!tsfSpeaker_ || !plstActiveAudio) return;

        // Convert SDK user list to vector for thread-safe callback
        std::vector<unsigned int> activeUserIds;
        int count = plstActiveAudio->GetCount();
        for (int i = 0; i < count; i++) {
            activeUserIds.push_back(plstActiveAudio->GetItem(i));
        }

        // Call JavaScript callback via ThreadSafeFunction
        tsfSpeaker_.NonBlockingCall(
            [activeUserIds](Napi::Env env, Napi::Function jsCallback) {
                Napi::Array arr = Napi::Array::New(env, activeUserIds.size());
                for (size_t i = 0; i < activeUserIds.size(); i++) {
                    arr[i] = Napi::Number::New(env, activeUserIds[i]);
                }
                jsCallback.Call({ arr });
            }
        );
    }

    // Other audio events (all pure virtual methods must be implemented)
    void onUserAudioStatusChange(IList<IUserAudioStatus*>*, const zchar_t* = nullptr) override {}
    void onHostRequestStartAudio(IRequestStartAudioHandler*) override {}
    void onJoin3rdPartyTelephonyAudio(const zchar_t*) override {}
    void onMuteOnEntryStatusChange(bool) override {}
};

// ============================================================
// Audio Raw Data Delegate
// ============================================================
class AudioDelegate : public IZoomSDKAudioRawDataDelegate {
public:
    Napi::ThreadSafeFunction tsf_;

    void onMixedAudioRawDataReceived(AudioRawData* data) override {
        if (!tsf_ || !data) return;
        unsigned int len = data->GetBufferLen();
        if (len == 0) return;
        unsigned int sampleRate = data->GetSampleRate();
        // Copy data before returning (SDK-owned pointer)
        std::vector<char> buffer(data->GetBuffer(), data->GetBuffer() + len);
        tsf_.NonBlockingCall([buf = std::move(buffer), sampleRate](Napi::Env env, Napi::Function jsCallback) mutable {
            Napi::Buffer<char> nodeBuf = Napi::Buffer<char>::Copy(env, buf.data(), buf.size());
            jsCallback.Call({nodeBuf, Napi::Number::New(env, (double)sampleRate)});
        });
    }

    void onOneWayAudioRawDataReceived(AudioRawData*, uint32_t) override {}
    void onShareAudioRawDataReceived(AudioRawData*, uint32_t) override {}
    void onOneWayInterpreterAudioRawDataReceived(AudioRawData*, const zchar_t*) override {}
};

// ============================================================
// Main ZoomSDK Node.js class
// ============================================================
class ZoomSDKNode : public Napi::ObjectWrap<ZoomSDKNode> {
public:
    static Napi::Object Init(Napi::Env env, Napi::Object exports);
    ZoomSDKNode(const Napi::CallbackInfo& info);
    ~ZoomSDKNode();

private:
    // N-API methods
    Napi::Value Initialize(const Napi::CallbackInfo& info);
    Napi::Value Authenticate(const Napi::CallbackInfo& info);
    Napi::Value JoinMeeting(const Napi::CallbackInfo& info);
    Napi::Value JoinAudio(const Napi::CallbackInfo& info);
    Napi::Value LeaveMeeting(const Napi::CallbackInfo& info);
    Napi::Value StartRecording(const Napi::CallbackInfo& info);
    Napi::Value StopRecording(const Napi::CallbackInfo& info);
    Napi::Value Cleanup(const Napi::CallbackInfo& info);
    Napi::Value OnAuthResult(const Napi::CallbackInfo& info);
    Napi::Value OnMeetingStatus(const Napi::CallbackInfo& info);
    Napi::Value OnAudioData(const Napi::CallbackInfo& info);
    Napi::Value OnActiveSpeakerChange(const Napi::CallbackInfo& info);
    Napi::Value GetUserInfo(const Napi::CallbackInfo& info);

    // SDK objects
    IAuthService*               authService_    = nullptr;
    IMeetingService*            meetingService_ = nullptr;
    IMeetingAudioController*    audioController_ = nullptr;
    IZoomSDKAudioRawDataHelper* audioHelper_    = nullptr;

    // Event handlers
    AuthEventHandler    authHandler_;
    MeetingEventHandler meetingHandler_;
    AudioDelegate       audioDelegate_;
    AudioEventHandler   audioEventHandler_;

    bool initialized_ = false;

    // String storage (must outlive SDK calls)
    std::string jwtStorage_;
    std::string meetingNumStorage_;
    std::string displayNameStorage_;
    std::string passwordStorage_;
    std::string onBehalfTokenStorage_;
};

ZoomSDKNode::ZoomSDKNode(const Napi::CallbackInfo& info)
    : Napi::ObjectWrap<ZoomSDKNode>(info) {
    std::cout << "[ZoomSDK] Constructor" << std::endl;
}

ZoomSDKNode::~ZoomSDKNode() {
    if (audioHelper_) {
        audioHelper_->unSubscribe();
        audioHelper_ = nullptr;
    }
    if (meetingService_) {
        DestroyMeetingService(meetingService_);
        meetingService_ = nullptr;
    }
    if (authService_) {
        DestroyAuthService(authService_);
        authService_ = nullptr;
    }
    if (initialized_) {
        CleanUPSDK();
        initialized_ = false;
    }
}

Napi::Object ZoomSDKNode::Init(Napi::Env env, Napi::Object exports) {
    Napi::Function func = DefineClass(env, "ZoomSDK", {
        InstanceMethod("initialize",      &ZoomSDKNode::Initialize),
        InstanceMethod("authenticate",    &ZoomSDKNode::Authenticate),
        InstanceMethod("joinMeeting",     &ZoomSDKNode::JoinMeeting),
        InstanceMethod("joinAudio",       &ZoomSDKNode::JoinAudio),
        InstanceMethod("leaveMeeting",    &ZoomSDKNode::LeaveMeeting),
        InstanceMethod("startRecording",  &ZoomSDKNode::StartRecording),
        InstanceMethod("stopRecording",   &ZoomSDKNode::StopRecording),
        InstanceMethod("cleanup",         &ZoomSDKNode::Cleanup),
        InstanceMethod("onAuthResult",           &ZoomSDKNode::OnAuthResult),
        InstanceMethod("onMeetingStatus",        &ZoomSDKNode::OnMeetingStatus),
        InstanceMethod("onAudioData",            &ZoomSDKNode::OnAudioData),
        InstanceMethod("onActiveSpeakerChange",  &ZoomSDKNode::OnActiveSpeakerChange),
        InstanceMethod("getUserInfo",            &ZoomSDKNode::GetUserInfo),
    });
    exports.Set("ZoomSDK", func);
    return exports;
}

Napi::Value ZoomSDKNode::Initialize(const Napi::CallbackInfo& info) {
    std::cout << "[ZoomSDK] Initialize" << std::endl;
    Napi::Env env = info.Env();

    // Ensure Qt event loop is running before initializing SDK
    ensureQtApp(env);

    InitParam initParam = {};
    initParam.strWebDomain       = "https://zoom.us";
    initParam.enableLogByDefault = true;
    initParam.uiLogFileSize      = 5;
    // Enable raw audio data capture
    initParam.rawdataOpts.enableRawdataIntermediateMode = false;
    initParam.rawdataOpts.audioRawdataMemoryMode = ZoomSDKRawDataMemoryModeStack;

    SDKError err = InitSDK(initParam);
    if (err != SDKERR_SUCCESS) {
        std::cerr << "[ZoomSDK] InitSDK failed: " << (int)err << std::endl;
        Napi::Error::New(env, "InitSDK failed: " + std::to_string((int)err))
            .ThrowAsJavaScriptException();
        return env.Undefined();
    }

    err = CreateAuthService(&authService_);
    if (err != SDKERR_SUCCESS) {
        std::cerr << "[ZoomSDK] CreateAuthService failed: " << (int)err << std::endl;
        Napi::Error::New(env, "CreateAuthService failed: " + std::to_string((int)err))
            .ThrowAsJavaScriptException();
        return env.Undefined();
    }
    authService_->SetEvent(&authHandler_);

    err = CreateMeetingService(&meetingService_);
    if (err != SDKERR_SUCCESS) {
        std::cerr << "[ZoomSDK] CreateMeetingService failed: " << (int)err << std::endl;
        Napi::Error::New(env, "CreateMeetingService failed: " + std::to_string((int)err))
            .ThrowAsJavaScriptException();
        return env.Undefined();
    }
    meetingService_->SetEvent(&meetingHandler_);

    initialized_ = true;
    std::cout << "[ZoomSDK] Initialized successfully" << std::endl;
    return env.Undefined();
}

Napi::Value ZoomSDKNode::Authenticate(const Napi::CallbackInfo& info) {
    std::cout << "[ZoomSDK] Authenticate" << std::endl;
    Napi::Env env = info.Env();

    if (info.Length() < 1 || !info[0].IsObject()) {
        Napi::TypeError::New(env, "Expected object with jwt field")
            .ThrowAsJavaScriptException();
        return env.Undefined();
    }

    Napi::Object opts = info[0].As<Napi::Object>();
    jwtStorage_ = opts.Get("jwt").As<Napi::String>().Utf8Value();

    AuthContext authCtx = {};
    authCtx.jwt_token = jwtStorage_.c_str();

    SDKError err = authService_->SDKAuth(authCtx);
    if (err != SDKERR_SUCCESS) {
        std::cerr << "[ZoomSDK] SDKAuth failed: " << (int)err << std::endl;
        Napi::Error::New(env, "SDKAuth failed: " + std::to_string((int)err))
            .ThrowAsJavaScriptException();
        return env.Undefined();
    }

    return env.Undefined();
}

Napi::Value ZoomSDKNode::JoinMeeting(const Napi::CallbackInfo& info) {
    std::cout << "[ZoomSDK] JoinMeeting" << std::endl;
    Napi::Env env = info.Env();

    if (info.Length() < 1 || !info[0].IsObject()) {
        Napi::TypeError::New(env, "Expected object").ThrowAsJavaScriptException();
        return env.Undefined();
    }

    Napi::Object opts = info[0].As<Napi::Object>();
    meetingNumStorage_  = opts.Get("meetingNumber").As<Napi::String>().Utf8Value();
    displayNameStorage_ = opts.Get("displayName").As<Napi::String>().Utf8Value();
    passwordStorage_    = "";
    onBehalfTokenStorage_ = "";
    if (opts.Has("password") && !opts.Get("password").IsNull()
            && opts.Get("password").IsString()) {
        passwordStorage_ = opts.Get("password").As<Napi::String>().Utf8Value();
    }
    if (opts.Has("onBehalfToken") && !opts.Get("onBehalfToken").IsNull()
            && opts.Get("onBehalfToken").IsString()) {
        onBehalfTokenStorage_ = opts.Get("onBehalfToken").As<Napi::String>().Utf8Value();
    }

    JoinParam joinParam = {};
    joinParam.userType = SDK_UT_WITHOUT_LOGIN;

    JoinParam4WithoutLogin& param = joinParam.param.withoutloginuserJoin;
    param = {};
    try {
        param.meetingNumber = (UINT64)std::stoull(meetingNumStorage_);
    } catch (...) {
        Napi::Error::New(env, "Invalid meeting number: " + meetingNumStorage_)
            .ThrowAsJavaScriptException();
        return env.Undefined();
    }
    param.userName                  = displayNameStorage_.c_str();
    param.psw                       = passwordStorage_.empty() ? nullptr : passwordStorage_.c_str();
    param.onBehalfToken             = onBehalfTokenStorage_.empty() ? nullptr : onBehalfTokenStorage_.c_str();
    param.isVideoOff                = true;
    param.isAudioOff                = false;
    param.eAudioRawdataSamplingRate = AudioRawdataSamplingRate_32K;
    param.isAudioRawDataStereo      = false;
    param.isMyVoiceInMix            = false;

    SDKError err = meetingService_->Join(joinParam);
    if (err != SDKERR_SUCCESS) {
        std::cerr << "[ZoomSDK] Join failed: " << (int)err << std::endl;
        Napi::Error::New(env, "Join failed: " + std::to_string((int)err))
            .ThrowAsJavaScriptException();
        return env.Undefined();
    }

    return env.Undefined();
}

Napi::Value ZoomSDKNode::JoinAudio(const Napi::CallbackInfo& info) {
    std::cout << "[ZoomSDK] JoinAudio - joining VoIP" << std::endl;
    Napi::Env env = info.Env();

    if (!meetingService_) {
        Napi::Error::New(env, "Meeting service not initialized")
            .ThrowAsJavaScriptException();
        return env.Undefined();
    }

    // Get audio controller
    audioController_ = meetingService_->GetMeetingAudioController();
    if (!audioController_) {
        std::cerr << "[ZoomSDK] GetMeetingAudioController failed" << std::endl;
        Napi::Error::New(env, "Failed to get audio controller")
            .ThrowAsJavaScriptException();
        return env.Undefined();
    }

    // Join VoIP audio
    SDKError err = audioController_->JoinVoip();
    if (err != SDKERR_SUCCESS) {
        std::cerr << "[ZoomSDK] JoinVoip failed: " << (int)err << std::endl;
        Napi::Error::New(env, "JoinVoip failed: " + std::to_string((int)err))
            .ThrowAsJavaScriptException();
        return env.Undefined();
    }

    std::cout << "[ZoomSDK] Successfully joined VoIP audio" << std::endl;
    return env.Undefined();
}

Napi::Value ZoomSDKNode::LeaveMeeting(const Napi::CallbackInfo& info) {
    std::cout << "[ZoomSDK] LeaveMeeting" << std::endl;
    if (meetingService_) {
        meetingService_->Leave(LEAVE_MEETING);
    }
    return info.Env().Undefined();
}

Napi::Value ZoomSDKNode::StartRecording(const Napi::CallbackInfo& info) {
    std::cout << "[ZoomSDK] StartRecording" << std::endl;
    Napi::Env env = info.Env();

    audioHelper_ = GetAudioRawdataHelper();
    if (!audioHelper_) {
        std::cerr << "[ZoomSDK] GetAudioRawdataHelper returned null" << std::endl;
        Napi::Error::New(env, "GetAudioRawdataHelper failed")
            .ThrowAsJavaScriptException();
        return env.Undefined();
    }

    SDKError err = audioHelper_->subscribe(&audioDelegate_);
    if (err != SDKERR_SUCCESS) {
        std::cerr << "[ZoomSDK] Audio subscribe failed: " << (int)err << std::endl;
        Napi::Error::New(env, "Audio subscribe failed: " + std::to_string((int)err))
            .ThrowAsJavaScriptException();
        return env.Undefined();
    }

    std::cout << "[ZoomSDK] Audio recording started" << std::endl;
    return env.Undefined();
}

Napi::Value ZoomSDKNode::StopRecording(const Napi::CallbackInfo& info) {
    std::cout << "[ZoomSDK] StopRecording" << std::endl;
    if (audioHelper_) {
        audioHelper_->unSubscribe();
        audioHelper_ = nullptr;
    }
    return info.Env().Undefined();
}

Napi::Value ZoomSDKNode::Cleanup(const Napi::CallbackInfo& info) {
    std::cout << "[ZoomSDK] Cleanup" << std::endl;
    if (audioHelper_) {
        audioHelper_->unSubscribe();
        audioHelper_ = nullptr;
    }
    if (meetingService_) {
        DestroyMeetingService(meetingService_);
        meetingService_ = nullptr;
    }
    if (authService_) {
        DestroyAuthService(authService_);
        authService_ = nullptr;
    }
    if (initialized_) {
        CleanUPSDK();
        initialized_ = false;
    }
    if (g_qtApp) {
        g_qtApp->quit();
    }
    return info.Env().Undefined();
}

Napi::Value ZoomSDKNode::OnAuthResult(const Napi::CallbackInfo& info) {
    Napi::Env env = info.Env();
    if (info.Length() < 1 || !info[0].IsFunction()) {
        Napi::TypeError::New(env, "Expected function").ThrowAsJavaScriptException();
        return env.Undefined();
    }
    authHandler_.tsf_ = Napi::ThreadSafeFunction::New(
        env, info[0].As<Napi::Function>(), "authCallback", 0, 1);
    return env.Undefined();
}

Napi::Value ZoomSDKNode::OnMeetingStatus(const Napi::CallbackInfo& info) {
    Napi::Env env = info.Env();
    if (info.Length() < 1 || !info[0].IsFunction()) {
        Napi::TypeError::New(env, "Expected function").ThrowAsJavaScriptException();
        return env.Undefined();
    }
    // Release previous TSF if exists
    if (meetingHandler_.tsf_) {
        meetingHandler_.tsf_.Release();
    }
    meetingHandler_.tsf_ = Napi::ThreadSafeFunction::New(
        env, info[0].As<Napi::Function>(), "statusCallback", 0, 1);
    return env.Undefined();
}

Napi::Value ZoomSDKNode::OnAudioData(const Napi::CallbackInfo& info) {
    Napi::Env env = info.Env();
    if (info.Length() < 1 || !info[0].IsFunction()) {
        Napi::TypeError::New(env, "Expected function").ThrowAsJavaScriptException();
        return env.Undefined();
    }
    audioDelegate_.tsf_ = Napi::ThreadSafeFunction::New(
        env, info[0].As<Napi::Function>(), "audioCallback", 0, 1);
    return env.Undefined();
}

Napi::Value ZoomSDKNode::OnActiveSpeakerChange(const Napi::CallbackInfo& info) {
    Napi::Env env = info.Env();
    if (info.Length() < 1 || !info[0].IsFunction()) {
        Napi::TypeError::New(env, "Expected function").ThrowAsJavaScriptException();
        return env.Undefined();
    }

    // Create ThreadSafeFunction for speaker events
    audioEventHandler_.tsfSpeaker_ = Napi::ThreadSafeFunction::New(
        env, info[0].As<Napi::Function>(), "speakerCallback", 0, 1
    );

    // Register event handler with audio controller
    if (audioController_) {
        audioController_->SetEvent(&audioEventHandler_);
        std::cout << "[ZoomSDK] Speaker event handler registered" << std::endl;
    } else {
        std::cerr << "[ZoomSDK] Audio controller not available for speaker events" << std::endl;
    }

    return env.Undefined();
}

Napi::Value ZoomSDKNode::GetUserInfo(const Napi::CallbackInfo& info) {
    Napi::Env env = info.Env();
    if (info.Length() < 1 || !info[0].IsNumber()) {
        Napi::TypeError::New(env, "Expected user ID (number)").ThrowAsJavaScriptException();
        return env.Undefined();
    }

    unsigned int userId = info[0].As<Napi::Number>().Uint32Value();

    if (!meetingService_) {
        Napi::Error::New(env, "Meeting service not available").ThrowAsJavaScriptException();
        return env.Undefined();
    }

    IMeetingParticipantsController* participantsCtrl =
        meetingService_->GetMeetingParticipantsController();
    if (!participantsCtrl) {
        Napi::Error::New(env, "Participants controller not available").ThrowAsJavaScriptException();
        return env.Undefined();
    }

    IUserInfo* userInfo = participantsCtrl->GetUserByUserID(userId);
    if (!userInfo) {
        return env.Null();  // User not found (may have left)
    }

    Napi::Object result = Napi::Object::New(env);
    result.Set("userId", Napi::Number::New(env, userId));
    result.Set("userName", Napi::String::New(env, userInfo->GetUserName() ? userInfo->GetUserName() : "Unknown"));
    result.Set("isHost", Napi::Boolean::New(env, userInfo->IsHost()));

    return result;
}

// Module initialization
Napi::Object InitModule(Napi::Env env, Napi::Object exports) {
    return ZoomSDKNode::Init(env, exports);
}

NODE_API_MODULE(zoom_sdk_wrapper, InitModule)
