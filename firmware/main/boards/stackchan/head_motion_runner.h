#pragma once

#include "expression_recipe.h"

#include <atomic>
#include <cstddef>
#include <cstdint>
#include <string>

#include <freertos/FreeRTOS.h>
#include <freertos/task.h>

class XcBodyHeadMotionBackend {
public:
    virtual ~XcBodyHeadMotionBackend() = default;

    virtual void TickHeadMotion() = 0;
    virtual void MaintainIdleTorque() = 0;
    virtual bool HeadMotionCachedAt(int yaw, int pitch) = 0;
    virtual bool HeadMotionConfirmedAt(
        int yaw,
        int pitch,
        const char* diagnostic_context = nullptr,
        std::string* diagnostic_detail = nullptr) = 0;
    virtual bool SynchronizeHeadPosition(bool cancel_motion = false) = 0;
    virtual bool MoveHeadToIdle() = 0;
    virtual bool PrepareHeadMotion() = 0;
    virtual bool StartHeadCurve(
        const StackChanExpressionStep& curve) = 0;
    virtual bool HeadMotionInactive() = 0;
    virtual bool ConsumeHeadCurveFailure() = 0;
    virtual bool StartMeasuredIdleRecovery() = 0;
};

class XcBodyHeadMotionRunner {
public:
    explicit XcBodyHeadMotionRunner(XcBodyHeadMotionBackend& backend);

    bool StartTask();
    bool Start(
        const StackChanExpressionRecipe* recipe,
        bool restore_only);
    bool IsReady() const;
    void BeginPreparedTrajectory();
    bool RequestRecovery(
        StackChanExpressionOutcome outcome,
        const char* reason);
    bool TakeCompletion(
        StackChanExpressionOutcome& outcome,
        std::string& reason);

private:
    enum class Status : uint8_t {
        IDLE = 0,
        STARTING,
        CENTERING,
        READY,
        RUNNING_CURVE,
        PAUSING,
        RECOVERING,
        TERMINAL,
    };

    static constexpr uint64_t kStartupTimeoutUs = 5000000ULL;
    static constexpr uint64_t kExecutionMarginUs = 2000000ULL;
    static constexpr uint64_t kRecoveryTimeoutUs = 5000000ULL;
    static constexpr uint64_t kRecoveryRetryIntervalUs = 500000ULL;

    static void TaskTrampoline(void* arg);
    void TaskMain();
    void Advance();
    void AdvanceRecovery(uint64_t now_us);
    void ContinueFromIdle(uint64_t now_us);
    void StartFirstCurve(uint64_t now_us);
    void StartNextStep(uint64_t now_us);
    void BeginRecovery(
        StackChanExpressionOutcome outcome,
        uint64_t now_us,
        const char* reason);
    void FailAtExecutionDeadline(uint64_t now_us, Status status);
    void Complete(
        StackChanExpressionOutcome outcome,
        const std::string& reason = {});

    XcBodyHeadMotionBackend& backend_;
    TaskHandle_t task_handle_ = nullptr;
    std::atomic<Status> status_{Status::IDLE};
    std::atomic<bool> begin_requested_{false};
    std::atomic<bool> recovery_requested_{false};
    StackChanExpressionRecipe recipe_;
    bool restore_only_ = false;
    size_t step_index_ = 0;
    uint64_t startup_deadline_us_ = 0;
    uint64_t execution_deadline_us_ = 0;
    uint64_t hold_until_us_ = 0;
    uint64_t recovery_deadline_us_ = 0;
    uint64_t recovery_retry_at_us_ = 0;
    StackChanExpressionOutcome requested_outcome_ =
        StackChanExpressionOutcome::INTERRUPTED;
    StackChanExpressionOutcome recovery_outcome_ =
        StackChanExpressionOutcome::MOTION_FAILED;
    StackChanExpressionOutcome terminal_outcome_ =
        StackChanExpressionOutcome::UNAVAILABLE;
    std::string requested_reason_;
    std::string failure_reason_;
};
