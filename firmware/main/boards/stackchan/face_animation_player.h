#pragma once

#include <lvgl.h>

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
    bool ShowListening();
    bool ShowSpeaking();
    bool PrepareExpression(const std::string& animation);
    bool PlayPreparedExpression();
    bool ExpressionComplete();
    bool ExpressionFailed();

    bool ShowIdleLocked();
    bool ShowListeningLocked();
    void HideLocked();
    void PauseLocked();
    void ResumeLocked();
    bool IsVisibleLocked() const;

private:
    static constexpr uint32_t kIdleLoopDelayMs = 4000;

    bool ShowAssetLocked(
        const std::string& asset,
        int32_t loop_count,
        bool play);
    bool EnsureFaceObjectLocked();
    void InvalidateFaceAreaLocked(const lv_area_t& relative_area);
    void RecordDirtyAreaLocked(const lv_area_t& area);
    static bool SameImageLayout(
        const lv_img_dsc_t* first,
        const lv_img_dsc_t* second);

    LcdDisplay* display_;
    lv_obj_t* image_ = nullptr;
    lv_img_dsc_t image_source_ = {};
    bool image_source_installed_ = false;
    std::unique_ptr<LvglGif> gif_;
    bool paused_by_screensaver_ = false;
    bool dirty_area_valid_ = false;
    lv_area_t dirty_area_ = {};
};
