#pragma once

#include <functional>

#include <esp_timer.h>
#include <esp_pm.h>

#include "power_save_state.h"

class PowerSaveTimer {
public:
    PowerSaveTimer(int cpu_max_freq, int seconds_to_sleep = 20, int seconds_to_shutdown = -1);
    ~PowerSaveTimer();

    void SetEnabled(bool enabled);
    void OnEnterDimMode(std::function<void()> callback);
    void OnEnterSleepMode(std::function<void()> callback);
    void OnExitSleepMode(std::function<void()> callback);
    void OnShutdownRequest(std::function<void()> callback);
    void WakeUp();

private:
    void PowerSaveCheck();
    void ApplyTransition(PowerSaveTransition transition);

    esp_timer_handle_t power_save_timer_ = nullptr;
    PowerSaveState state_;
    bool is_wake_word_running_ = false;
    int cpu_max_freq_;
    int seconds_to_shutdown_;

    std::function<void()> on_enter_dim_mode_;
    std::function<void()> on_enter_sleep_mode_;
    std::function<void()> on_exit_sleep_mode_;
    std::function<void()> on_shutdown_request_;
};
