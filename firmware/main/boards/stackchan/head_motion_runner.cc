#include "head_motion_runner.h"

#include <esp_log.h>
#include <esp_timer.h>

#define TAG "XcBodyHeadMotion"

XcBodyHeadMotionRunner::XcBodyHeadMotionRunner(
        XcBodyHeadMotionBackend& backend)
    : backend_(backend) {}

bool XcBodyHeadMotionRunner::StartTask() {
    const BaseType_t created = xTaskCreatePinnedToCore(
        &XcBodyHeadMotionRunner::TaskTrampoline,
        "head_motion",
        4096,
        this,
        5,
        &task_handle_,
        1);
    if (created != pdPASS) {
        task_handle_ = nullptr;
        return false;
    }
    return true;
}

bool XcBodyHeadMotionRunner::Start(
        const StackChanExpressionRecipe* recipe,
        bool restore_only) {
    if (task_handle_ == nullptr ||
        status_.load(std::memory_order_acquire) != Status::IDLE) {
        return false;
    }
    if (recipe != nullptr) {
        recipe_ = *recipe;
    }
    restore_only_ = restore_only;
    step_index_ = 0;
    failure_reason_.clear();
    recovery_requested_.store(false, std::memory_order_relaxed);
    begin_requested_.store(false, std::memory_order_relaxed);
    startup_deadline_us_ = esp_timer_get_time() + kStartupTimeoutUs;
    status_.store(Status::STARTING, std::memory_order_release);
    return true;
}

bool XcBodyHeadMotionRunner::IsReady() const {
    return status_.load(std::memory_order_acquire) == Status::READY;
}

void XcBodyHeadMotionRunner::BeginPreparedTrajectory() {
    if (IsReady()) {
        begin_requested_.store(true, std::memory_order_release);
    }
}

bool XcBodyHeadMotionRunner::RequestRecovery(
        StackChanExpressionOutcome outcome,
        const char* reason) {
    const Status status = status_.load(std::memory_order_acquire);
    if (status == Status::IDLE || status == Status::RECOVERING ||
        status == Status::TERMINAL) {
        return false;
    }
    requested_outcome_ = outcome;
    requested_reason_ = reason != nullptr ? reason : "";
    recovery_requested_.store(true, std::memory_order_release);
    return true;
}

bool XcBodyHeadMotionRunner::TakeCompletion(
        StackChanExpressionOutcome& outcome,
        std::string& reason) {
    if (status_.load(std::memory_order_acquire) != Status::TERMINAL) {
        return false;
    }
    outcome = terminal_outcome_;
    reason = failure_reason_;
    status_.store(Status::IDLE, std::memory_order_release);
    return true;
}

void XcBodyHeadMotionRunner::TaskTrampoline(void* arg) {
    static_cast<XcBodyHeadMotionRunner*>(arg)->TaskMain();
}

void XcBodyHeadMotionRunner::TaskMain() {
    while (true) {
        backend_.TickHeadMotion();
        Advance();
        backend_.MaintainIdleTorque();
        taskYIELD();
    }
}

void XcBodyHeadMotionRunner::Complete(
        StackChanExpressionOutcome outcome,
        const std::string& reason) {
    terminal_outcome_ = outcome;
    failure_reason_ = reason;
    status_.store(Status::TERMINAL, std::memory_order_release);
}

void XcBodyHeadMotionRunner::ContinueFromIdle(uint64_t now_us) {
    if (restore_only_) {
        Complete(StackChanExpressionOutcome::COMPLETED);
        return;
    }
    if (!backend_.PrepareHeadMotion()) {
        BeginRecovery(
            StackChanExpressionOutcome::MOTION_FAILED,
            now_us,
            "torque_prepare_failed");
        return;
    }
    status_.store(Status::READY, std::memory_order_release);
}

void XcBodyHeadMotionRunner::StartFirstCurve(uint64_t now_us) {
    if (!backend_.StartHeadCurve(recipe_.steps[0])) {
        BeginRecovery(
            StackChanExpressionOutcome::MOTION_FAILED,
            now_us,
            "first_curve_start_failed");
        return;
    }
    step_index_ = 0;
    execution_deadline_us_ = now_us +
        static_cast<uint64_t>(
            StackChanExpressionRecipeDurationMs(recipe_)) * 1000ULL +
        kExecutionMarginUs;
    status_.store(Status::RUNNING_CURVE, std::memory_order_release);
}

void XcBodyHeadMotionRunner::StartNextStep(uint64_t now_us) {
    ++step_index_;
    if (step_index_ >= recipe_.step_count) {
        Complete(StackChanExpressionOutcome::COMPLETED);
        return;
    }
    const auto& next = recipe_.steps[step_index_];
    if (next.type == StackChanExpressionStepType::PAUSE) {
        hold_until_us_ = now_us +
            static_cast<uint64_t>(next.duration_ms) * 1000ULL;
        status_.store(Status::PAUSING, std::memory_order_release);
        return;
    }
    if (!backend_.StartHeadCurve(next)) {
        BeginRecovery(
            StackChanExpressionOutcome::MOTION_FAILED,
            now_us,
            "curve_start_failed");
        return;
    }
    status_.store(Status::RUNNING_CURVE, std::memory_order_release);
}

void XcBodyHeadMotionRunner::BeginRecovery(
        StackChanExpressionOutcome outcome,
        uint64_t now_us,
        const char* reason) {
    recovery_outcome_ = outcome;
    failure_reason_ = reason != nullptr ? reason : "";
    ESP_LOGW(
        TAG,
        "Recovery started: reason=%s outcome=%s step_index=%u",
        reason != nullptr ? reason : "",
        StackChanExpressionOutcomeName(outcome),
        static_cast<unsigned>(step_index_));
    if (backend_.HeadMotionConfirmedAt(
            kStackChanExpressionIdleYaw,
            kStackChanExpressionIdlePitch,
            "recovery entry")) {
        Complete(outcome, failure_reason_);
        return;
    }
    recovery_deadline_us_ = now_us + kRecoveryTimeoutUs;
    recovery_retry_at_us_ = now_us + kRecoveryRetryIntervalUs;
    backend_.StartMeasuredIdleRecovery();
    status_.store(Status::RECOVERING, std::memory_order_release);
}

void XcBodyHeadMotionRunner::FailAtExecutionDeadline(
        uint64_t now_us,
        Status status) {
    std::string reason = "execution_deadline";
    if (status == Status::RUNNING_CURVE) {
        std::string detail;
        const auto& end = recipe_.steps[step_index_].points[3];
        if (backend_.HeadMotionConfirmedAt(
                end.yaw,
                end.pitch,
                "execution deadline",
                &detail)) {
            detail = "endpoint_confirmed_late";
        }
        if (!detail.empty()) {
            reason += ":";
            reason += detail;
        }
    }
    BeginRecovery(
        StackChanExpressionOutcome::MOTION_FAILED,
        now_us,
        reason.c_str());
}

void XcBodyHeadMotionRunner::AdvanceRecovery(uint64_t now_us) {
    const bool deadline_reached = now_us >= recovery_deadline_us_;
    const bool retry_due = !deadline_reached &&
        now_us >= recovery_retry_at_us_ && backend_.HeadMotionInactive();
    const char* diagnostic_context = deadline_reached
        ? "safe return deadline"
        : retry_due ? "idle return retry" : nullptr;
    if (backend_.HeadMotionConfirmedAt(
            kStackChanExpressionIdleYaw,
            kStackChanExpressionIdlePitch,
            diagnostic_context)) {
        Complete(recovery_outcome_, failure_reason_);
    } else if (deadline_reached) {
        if (!failure_reason_.empty()) {
            failure_reason_ += ":";
        }
        failure_reason_ += "recovery_deadline";
        Complete(
            StackChanExpressionOutcome::SAFE_RETURN_FAILED,
            failure_reason_);
    } else if (retry_due) {
        backend_.StartMeasuredIdleRecovery();
        recovery_retry_at_us_ = now_us + kRecoveryRetryIntervalUs;
    }
}

void XcBodyHeadMotionRunner::Advance() {
    const Status status = status_.load(std::memory_order_acquire);
    if (status == Status::IDLE || status == Status::TERMINAL) {
        return;
    }
    const uint64_t now_us = esp_timer_get_time();
    if (status != Status::RECOVERING &&
        recovery_requested_.exchange(false, std::memory_order_acq_rel)) {
        BeginRecovery(
            requested_outcome_, now_us, requested_reason_.c_str());
        return;
    }
    if (status == Status::RUNNING_CURVE &&
        backend_.ConsumeHeadCurveFailure()) {
        BeginRecovery(
            StackChanExpressionOutcome::MOTION_FAILED,
            now_us,
            "curve_driver_failed");
        return;
    }
    if ((status == Status::RUNNING_CURVE || status == Status::PAUSING) &&
        now_us >= execution_deadline_us_) {
        FailAtExecutionDeadline(now_us, status);
        return;
    }

    switch (status) {
        case Status::STARTING:
            if (backend_.HeadMotionCachedAt(
                    kStackChanExpressionIdleYaw,
                    kStackChanExpressionIdlePitch) &&
                backend_.HeadMotionConfirmedAt(
                    kStackChanExpressionIdleYaw,
                    kStackChanExpressionIdlePitch)) {
                ContinueFromIdle(now_us);
            } else if (!backend_.SynchronizeHeadPosition() ||
                       !backend_.MoveHeadToIdle()) {
                BeginRecovery(
                    StackChanExpressionOutcome::MOTION_FAILED,
                    now_us,
                    "startup_center_failed");
            } else {
                status_.store(Status::CENTERING, std::memory_order_release);
            }
            break;
        case Status::CENTERING:
            if (backend_.HeadMotionCachedAt(
                    kStackChanExpressionIdleYaw,
                    kStackChanExpressionIdlePitch) &&
                backend_.HeadMotionConfirmedAt(
                    kStackChanExpressionIdleYaw,
                    kStackChanExpressionIdlePitch)) {
                ContinueFromIdle(now_us);
            } else if (now_us >= startup_deadline_us_) {
                BeginRecovery(
                    StackChanExpressionOutcome::MOTION_FAILED,
                    now_us,
                    "startup_deadline");
            }
            break;
        case Status::READY:
            if (begin_requested_.exchange(
                    false, std::memory_order_acq_rel)) {
                StartFirstCurve(now_us);
            }
            break;
        case Status::RUNNING_CURVE: {
            const auto& end = recipe_.steps[step_index_].points[3];
            if (backend_.HeadMotionInactive() &&
                backend_.HeadMotionConfirmedAt(end.yaw, end.pitch)) {
                StartNextStep(now_us);
            }
            break;
        }
        case Status::PAUSING:
            if (now_us >= hold_until_us_) {
                StartNextStep(now_us);
            }
            break;
        case Status::RECOVERING:
            AdvanceRecovery(now_us);
            break;
        case Status::IDLE:
        case Status::TERMINAL:
            break;
    }
}
