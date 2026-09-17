#pragma once

#include <lvgl.h>

#include <atomic>
#include <cstdint>
#include <memory>
#include <string>

class LcdDisplay;
class LvglGif;

class XcBodyFaceAnimationPlayer {
public:
    explicit XcBodyFaceAnimationPlayer(LcdDisplay* display);
    ~XcBodyFaceAnimationPlayer();

    bool ShowIdle();
    void RequestIdle();
    void RequestListening();
    void RequestSpeaking();
    bool PrepareExpression(const std::string& animation);
    bool PlayPreparedExpression();
    bool ExpressionComplete();
    bool ExpressionFailed();

    void HideLocked();
    void PauseLocked();
    void ResumeLocked();
    bool IsVisibleLocked() const;

private:
    static constexpr uint32_t kIdleLoopDelayMs = 4000;
    static constexpr uint32_t kFaceRequestIntervalMs = 20;

    enum class StateFace : uint8_t {
        NONE,
        IDLE,
        LISTENING,
        SPEAKING,
    };

    bool ShowIdleLocked();
    bool ShowListeningLocked();
    bool ShowAssetLocked(
        const std::string& asset,
        int32_t loop_count,
        bool play);
    void RequestFace(StateFace face);
    void ApplyRequestedFaceLocked();
    void CancelRequestedFaceLocked();
    static void FaceRequestTimerCallback(lv_timer_t* timer);
    bool EnsureFaceObjectLocked();
    void InvalidateFaceAreaLocked(const lv_area_t& relative_area);
    static bool SameImageLayout(
        const lv_img_dsc_t* first,
        const lv_img_dsc_t* second);

    LcdDisplay* display_;
    lv_obj_t* image_ = nullptr;
    lv_img_dsc_t image_source_ = {};
    bool image_source_installed_ = false;
    std::unique_ptr<LvglGif> gif_;
    bool paused_by_screensaver_ = false;
    std::atomic<StateFace> requested_face_{StateFace::NONE};
    StateFace applied_face_ = StateFace::NONE;
    lv_timer_t* face_request_timer_ = nullptr;
};
