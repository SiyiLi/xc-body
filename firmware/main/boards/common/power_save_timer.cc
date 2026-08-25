#include "power_save_timer.h"
#include "application.h"
#include "settings.h"

#include <esp_log.h>
#if defined(CONFIG_BOARD_TYPE_STACKCHAN) && \
    defined(CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG)
#include <driver/usb_serial_jtag.h>
#endif
#define TAG "PowerSaveTimer"


PowerSaveTimer::PowerSaveTimer(
        int cpu_max_freq,
        int seconds_to_sleep,
        int seconds_to_shutdown)
    : state_(seconds_to_sleep),
      cpu_max_freq_(cpu_max_freq),
      seconds_to_shutdown_(seconds_to_shutdown) {
    esp_timer_create_args_t timer_args = {
        .callback = [](void* arg) {
            auto self = static_cast<PowerSaveTimer*>(arg);
            self->PowerSaveCheck();
        },
        .arg = this,
        .dispatch_method = ESP_TIMER_TASK,
        .name = "power_save_timer",
        .skip_unhandled_events = true,
    };
    ESP_ERROR_CHECK(esp_timer_create(&timer_args, &power_save_timer_));
}

PowerSaveTimer::~PowerSaveTimer() {
    esp_timer_stop(power_save_timer_);
    esp_timer_delete(power_save_timer_);
}

void PowerSaveTimer::SetEnabled(bool enabled) {
    if (enabled && !state_.enabled()) {
        Settings settings("wifi", false);
        if (!settings.GetBool("sleep_mode", true)) {
            ESP_LOGI(TAG, "Power save timer is disabled by settings");
            return;
        }

        state_.SetEnabled(true);
        ESP_ERROR_CHECK(esp_timer_start_periodic(
            power_save_timer_, 1000000));
        ESP_LOGI(TAG, "Power save timer enabled");
    } else if (!enabled && state_.enabled()) {
        ESP_ERROR_CHECK(esp_timer_stop(power_save_timer_));
        ApplyTransition(state_.SetEnabled(false));
        ESP_LOGI(TAG, "Power save timer disabled");
    }
}

void PowerSaveTimer::OnEnterDimMode(std::function<void()> callback) {
    on_enter_dim_mode_ = callback;
}

void PowerSaveTimer::OnEnterSleepMode(std::function<void()> callback) {
    on_enter_sleep_mode_ = callback;
}

void PowerSaveTimer::OnExitSleepMode(std::function<void()> callback) {
    on_exit_sleep_mode_ = callback;
}

void PowerSaveTimer::OnShutdownRequest(std::function<void()> callback) {
    on_shutdown_request_ = callback;
}

void PowerSaveTimer::PowerSaveCheck() {
#if defined(CONFIG_BOARD_TYPE_STACKCHAN) && \
    defined(CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG)
    if (usb_serial_jtag_is_connected()) {
        WakeUp();
        return;
    }
#endif
    auto& app = Application::GetInstance();
    ApplyTransition(state_.Tick(app.CanEnterSleepMode()));
    if (
        seconds_to_shutdown_ != -1 &&
        state_.ticks() >= seconds_to_shutdown_ &&
        on_shutdown_request_
    ) {
        on_shutdown_request_();
    }
}

void PowerSaveTimer::ApplyTransition(PowerSaveTransition transition) {
    switch (transition) {
        case PowerSaveTransition::NONE:
            return;
        case PowerSaveTransition::ENTER_DIM:
            if (on_enter_dim_mode_) {
                on_enter_dim_mode_();
            }
            return;
        case PowerSaveTransition::ENTER_SLEEP:
            ESP_LOGI(TAG, "Enabling power save mode");
            if (on_enter_sleep_mode_) {
                on_enter_sleep_mode_();
            }
            if (cpu_max_freq_ != -1) {
                auto& audio_service =
                    Application::GetInstance().GetAudioService();
                is_wake_word_running_ =
                    audio_service.IsWakeWordRunning();
                if (is_wake_word_running_) {
                    audio_service.EnableWakeWordDetection(false);
                    vTaskDelay(pdMS_TO_TICKS(100));
                }
                auto codec = Board::GetInstance().GetAudioCodec();
                if (codec) {
                    codec->EnableInput(false);
                }
                esp_pm_config_t pm_config = {
                    .max_freq_mhz = cpu_max_freq_,
                    .min_freq_mhz = 40,
                    .light_sleep_enable = true,
                };
                esp_pm_configure(&pm_config);
            }
            return;
        case PowerSaveTransition::EXIT_SLEEP:
            ESP_LOGI(TAG, "Exiting power save mode");
            if (cpu_max_freq_ != -1) {
                esp_pm_config_t pm_config = {
                    .max_freq_mhz = cpu_max_freq_,
                    .min_freq_mhz = cpu_max_freq_,
                    .light_sleep_enable = false,
                };
                esp_pm_configure(&pm_config);

                auto& audio_service =
                    Application::GetInstance().GetAudioService();
                if (is_wake_word_running_) {
                    audio_service.EnableWakeWordDetection(true);
                }
            }
            if (on_exit_sleep_mode_) {
                on_exit_sleep_mode_();
            }
            return;
        case PowerSaveTransition::EXIT_DIM:
            if (on_exit_sleep_mode_) {
                on_exit_sleep_mode_();
            }
            return;
    }
}

void PowerSaveTimer::WakeUp() {
    ApplyTransition(state_.WakeUp());
}
