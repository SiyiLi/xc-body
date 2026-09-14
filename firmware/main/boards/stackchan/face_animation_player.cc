#include "face_animation_player.h"

#include "assets.h"
#include "display/lcd_display.h"
#include "display/lvgl_display/gif/lvgl_gif.h"
#include "expression_recipe.h"

#include <esp_log.h>

#define TAG "XcBodyFace"

XcBodyFaceAnimationPlayer::XcBodyFaceAnimationPlayer(LcdDisplay* display)
    : display_(display) {}

XcBodyFaceAnimationPlayer::~XcBodyFaceAnimationPlayer() = default;

bool XcBodyFaceAnimationPlayer::SameImageLayout(
        const lv_img_dsc_t* first,
        const lv_img_dsc_t* second) {
    return first != nullptr && second != nullptr &&
        first->header.w == second->header.w &&
        first->header.h == second->header.h &&
        first->header.cf == second->header.cf &&
        first->header.stride == second->header.stride;
}

void XcBodyFaceAnimationPlayer::InvalidateFaceAreaLocked(
        const lv_area_t& relative_area) {
    if (image_ == nullptr || !lv_obj_is_valid(image_)) {
        return;
    }
    lv_area_t face_area;
    lv_obj_get_coords(image_, &face_area);
    const lv_area_t dirty_area = {
        .x1 = face_area.x1 + relative_area.x1,
        .y1 = face_area.y1 + relative_area.y1,
        .x2 = face_area.x1 + relative_area.x2,
        .y2 = face_area.y1 + relative_area.y2,
    };
    lv_obj_invalidate_area(image_, &dirty_area);
}

bool XcBodyFaceAnimationPlayer::EnsureFaceObjectLocked() {
    if (image_ != nullptr && lv_obj_is_valid(image_)) {
        return true;
    }
    lv_obj_t* screen = lv_screen_active();
    if (screen == nullptr) {
        return false;
    }
    image_ = lv_image_create(screen);
    if (image_ == nullptr) {
        return false;
    }
    image_source_installed_ = false;
    lv_obj_align(image_, LV_ALIGN_CENTER, 0, 0);
    lv_image_set_scale(image_, 256);
    lv_obj_clear_flag(image_, LV_OBJ_FLAG_SCROLLABLE);
    display_->PlaceBehindStatusBarLocked(image_);
    return true;
}

bool XcBodyFaceAnimationPlayer::ShowAssetLocked(
        const std::string& asset,
        int32_t loop_count,
        bool play) {
    void* data = nullptr;
    size_t size = 0;
    if (!Assets::GetInstance().GetAssetData(asset, data, size) ||
        data == nullptr || size == 0 || !EnsureFaceObjectLocked()) {
        ESP_LOGW(TAG, "Face asset unavailable: %s", asset.c_str());
        return false;
    }

    lv_img_dsc_t source = {};
    source.data = static_cast<const uint8_t*>(data);
    source.data_size = size;
    auto next_gif = std::make_unique<LvglGif>(&source);
    if (!next_gif->IsLoaded()) {
        ESP_LOGW(TAG, "Face GIF could not be decoded: %s", asset.c_str());
        return false;
    }
    next_gif->SetLoopCount(loop_count);
    next_gif->Start();
    if (!play) {
        next_gif->Pause();
    }

    const lv_img_dsc_t* previous_image =
        gif_ != nullptr ? gif_->image_dsc() : nullptr;
    const bool reuse_source = image_source_installed_ &&
        SameImageLayout(previous_image, next_gif->image_dsc());

    gif_ = std::move(next_gif);
    paused_by_screensaver_ = false;
    image_source_ = *gif_->image_dsc();
    gif_->SetFrameCallback([this](const lv_area_t& area) {
        InvalidateFaceAreaLocked(area);
    });
    if (!reuse_source) {
        lv_image_set_src(image_, &image_source_);
        image_source_installed_ = true;
    }
    lv_obj_clear_flag(image_, LV_OBJ_FLAG_HIDDEN);
    // The first frame is decoded before its callback is installed. Redraw the
    // complete face once on transition; later frames invalidate only changes.
    lv_obj_invalidate(image_);
    display_->PlaceBehindStatusBarLocked(image_);
    return true;
}

bool XcBodyFaceAnimationPlayer::ShowIdleLocked() {
    if (!ShowAssetLocked("idle.gif", 0, true)) {
        return false;
    }
    gif_->SetLoopDelay(kIdleLoopDelayMs);
    return true;
}

bool XcBodyFaceAnimationPlayer::ShowListeningLocked() {
    if (ShowAssetLocked("listening.gif", 0, true)) {
        return true;
    }
    return ShowIdleLocked();
}

bool XcBodyFaceAnimationPlayer::ShowIdle() {
    DisplayLockGuard lock(display_);
    return ShowIdleLocked();
}

bool XcBodyFaceAnimationPlayer::ShowListening() {
    DisplayLockGuard lock(display_);
    return ShowListeningLocked();
}

bool XcBodyFaceAnimationPlayer::ShowSpeaking() {
    DisplayLockGuard lock(display_);
    return ShowAssetLocked("speaking.gif", 0, true);
}

bool XcBodyFaceAnimationPlayer::PrepareExpression(
        const std::string& animation) {
    if (!IsStackChanExpressionName(animation)) {
        return false;
    }
    DisplayLockGuard lock(display_);
    return ShowAssetLocked(
        "expression-" + animation + ".gif", 1, false);
}

bool XcBodyFaceAnimationPlayer::PlayPreparedExpression() {
    DisplayLockGuard lock(display_);
    if (gif_ == nullptr || gif_->HasDecodeFailure()) {
        return false;
    }
    gif_->Resume();
    return gif_->IsPlaying();
}

bool XcBodyFaceAnimationPlayer::ExpressionComplete() {
    DisplayLockGuard lock(display_);
    return gif_ != nullptr && !gif_->IsPlaying();
}

bool XcBodyFaceAnimationPlayer::ExpressionFailed() {
    DisplayLockGuard lock(display_);
    return gif_ != nullptr && gif_->HasDecodeFailure();
}

void XcBodyFaceAnimationPlayer::HideLocked() {
    gif_.reset();
    paused_by_screensaver_ = false;
    if (image_ != nullptr && lv_obj_is_valid(image_)) {
        lv_obj_add_flag(image_, LV_OBJ_FLAG_HIDDEN);
    }
}

void XcBodyFaceAnimationPlayer::PauseLocked() {
    if (gif_ != nullptr && gif_->IsPlaying()) {
        gif_->Pause();
        paused_by_screensaver_ = true;
    }
}

void XcBodyFaceAnimationPlayer::ResumeLocked() {
    if (paused_by_screensaver_ && gif_ != nullptr && !gif_->IsPlaying() &&
        !gif_->HasDecodeFailure()) {
        gif_->Resume();
    }
    paused_by_screensaver_ = false;
}

bool XcBodyFaceAnimationPlayer::IsVisibleLocked() const {
    return image_ != nullptr && lv_obj_is_valid(image_) &&
        !lv_obj_has_flag(image_, LV_OBJ_FLAG_HIDDEN);
}
