#include "application.h"
#include "board.h"
#include "display.h"
#include "system_info.h"
#include "audio_codec.h"
#include "mqtt_protocol.h"
#include "websocket_protocol.h"
#include "assets/lang_config.h"
#include "mcp_server.h"
#include "assets.h"
#include "settings.h"

#include <cstring>
#include <esp_log.h>
#include <cJSON.h>
#include <driver/gpio.h>
#include <arpa/inet.h>
#include <font_awesome.h>

#define TAG "Application"

namespace {

constexpr auto kPlaybackDrainTimeout = std::chrono::seconds(5);

ListeningProfile ParseListenProfile(const cJSON* root) {
    auto profile = cJSON_GetObjectItem(root, "profile");
    bool profile_present = profile != nullptr;
    bool profile_is_string = cJSON_IsString(profile);
    auto result = ParseListenProfileField(profile_present,
                                          profile_is_string,
                                          profile_is_string ? profile->valuestring : nullptr);
    if (result.warning == kListenProfileParseWarningNonString) {
        ESP_LOGW(TAG, "listen profile is not a string; falling back to voice");
    } else if (result.warning == kListenProfileParseWarningUnknown) {
        ESP_LOGW(TAG, "Unknown listen profile: %s; falling back to voice",
                 profile->valuestring);
    }
    return result.profile;
}

} // namespace


Application::Application() {
    event_group_ = xEventGroupCreate();

#if CONFIG_USE_DEVICE_AEC && CONFIG_USE_SERVER_AEC
#error "CONFIG_USE_DEVICE_AEC and CONFIG_USE_SERVER_AEC cannot be enabled at the same time"
#elif CONFIG_USE_DEVICE_AEC
    aec_mode_ = kAecOnDeviceSide;
#elif CONFIG_USE_SERVER_AEC
    aec_mode_ = kAecOnServerSide;
#else
    aec_mode_ = kAecOff;
#endif

    esp_timer_create_args_t clock_timer_args = {
        .callback = [](void* arg) {
            Application* app = (Application*)arg;
            xEventGroupSetBits(app->event_group_, MAIN_EVENT_CLOCK_TICK);
        },
        .arg = this,
        .dispatch_method = ESP_TIMER_TASK,
        .name = "clock_timer",
        .skip_unhandled_events = true
    };
    esp_timer_create(&clock_timer_args, &clock_timer_handle_);
}

Application::~Application() {
    if (clock_timer_handle_ != nullptr) {
        esp_timer_stop(clock_timer_handle_);
        esp_timer_delete(clock_timer_handle_);
    }
    vEventGroupDelete(event_group_);
}

bool Application::SetDeviceState(DeviceState state) {
    return state_machine_.TransitionTo(state);
}

void Application::ResumePreparedAudioPlayback() {
    if (audio_service_.ReleasePreparedAudioPlayback()) {
        Board::GetInstance().OnTtsStart();
    }
}

void Application::Initialize() {
    auto& board = Board::GetInstance();
    SetDeviceState(kDeviceStateStarting);

    // Setup the display
    auto display = board.GetDisplay();
    display->SetupUI();
    // Print board name/version info
    display->SetChatMessage("system", SystemInfo::GetUserAgent().c_str());

    // Setup the audio service
    auto codec = board.GetAudioCodec();
    audio_service_.Initialize(codec);
    audio_service_.Start();

    AudioServiceCallbacks callbacks;
    callbacks.on_send_queue_available = [this]() {
        xEventGroupSetBits(event_group_, MAIN_EVENT_SEND_AUDIO);
    };
    callbacks.on_audio_output = [this]() {
        if (GetDeviceState() == kDeviceStateSpeaking) {
            Board::GetInstance().OnTtsAudioFrame();
        }
    };
    callbacks.on_wake_word_detected = [this](const std::string& wake_word) {
        xEventGroupSetBits(event_group_, MAIN_EVENT_WAKE_WORD_DETECTED);
    };
    callbacks.on_vad_change = [this](bool speaking) {
        xEventGroupSetBits(event_group_, MAIN_EVENT_VAD_CHANGE);
    };
    audio_service_.SetCallbacks(callbacks);

    // Add state change listeners
    state_machine_.AddStateChangeListener([this](DeviceState old_state, DeviceState new_state) {
        xEventGroupSetBits(event_group_, MAIN_EVENT_STATE_CHANGED);
    });

    // Start the clock timer to update the status bar
    esp_timer_start_periodic(clock_timer_handle_, 1000000);

    // Add MCP common tools (only once during initialization)
    auto& mcp_server = McpServer::GetInstance();
    mcp_server.AddCommonTools();
    mcp_server.AddUserOnlyTools();

    // Set network event callback for UI updates and network state handling
    board.SetNetworkEventCallback([this](NetworkEvent event, const std::string& data) {
        auto display = Board::GetInstance().GetDisplay();
        
        switch (event) {
            case NetworkEvent::Scanning:
                display->ShowNotification(Lang::Strings::SCANNING_WIFI, 30000);
                xEventGroupSetBits(event_group_, MAIN_EVENT_NETWORK_DISCONNECTED);
                break;
            case NetworkEvent::Connecting: {
                if (data.empty()) {
                    // Cellular network - registering without carrier info yet
                    display->SetStatus(Lang::Strings::REGISTERING_NETWORK);
                } else {
                    // WiFi or cellular with carrier info
                    std::string msg = Lang::Strings::CONNECT_TO;
                    msg += data;
                    msg += "...";
                    display->ShowNotification(msg.c_str(), 30000);
                }
                break;
            }
            case NetworkEvent::Connected: {
                std::string msg = Lang::Strings::CONNECTED_TO;
                msg += data;
                display->ShowNotification(msg.c_str(), 30000);
                xEventGroupSetBits(event_group_, MAIN_EVENT_NETWORK_CONNECTED);
                break;
            }
            case NetworkEvent::Disconnected:
                xEventGroupSetBits(event_group_, MAIN_EVENT_NETWORK_DISCONNECTED);
                break;
            case NetworkEvent::WifiConfigModeEnter:
                // WiFi config mode enter is handled by WifiBoard internally
                break;
            case NetworkEvent::WifiConfigModeExit:
                // WiFi config mode exit is handled by WifiBoard internally
                break;
            // Cellular modem specific events
            case NetworkEvent::ModemDetecting:
                display->SetStatus(Lang::Strings::DETECTING_MODULE);
                break;
            case NetworkEvent::ModemErrorNoSim:
                Alert(Lang::Strings::ERROR, Lang::Strings::PIN_ERROR, "triangle_exclamation", Lang::Sounds::OGG_ERR_PIN);
                break;
            case NetworkEvent::ModemErrorRegDenied:
                Alert(Lang::Strings::ERROR, Lang::Strings::REG_ERROR, "triangle_exclamation", Lang::Sounds::OGG_ERR_REG);
                break;
            case NetworkEvent::ModemErrorInitFailed:
                Alert(Lang::Strings::ERROR, Lang::Strings::MODEM_INIT_ERROR, "triangle_exclamation", Lang::Sounds::OGG_EXCLAMATION);
                break;
            case NetworkEvent::ModemErrorTimeout:
                display->SetStatus(Lang::Strings::REGISTERING_NETWORK);
                break;
        }
    });

    // Start network asynchronously
    board.StartNetwork();

    // Update the status bar immediately to show the network state
    display->UpdateStatusBar(true);
}

void Application::Run() {
    // Set the priority of the main task to 10
    vTaskPrioritySet(nullptr, 10);

    const EventBits_t ALL_EVENTS = 
        MAIN_EVENT_SCHEDULE |
        MAIN_EVENT_SEND_AUDIO |
        MAIN_EVENT_WAKE_WORD_DETECTED |
        MAIN_EVENT_VAD_CHANGE |
        MAIN_EVENT_CLOCK_TICK |
        MAIN_EVENT_ERROR |
        MAIN_EVENT_NETWORK_CONNECTED |
        MAIN_EVENT_NETWORK_DISCONNECTED |
        MAIN_EVENT_TOGGLE_CHAT |
        MAIN_EVENT_START_LISTENING |
        MAIN_EVENT_STOP_LISTENING |
        MAIN_EVENT_CANCEL_LISTENING |
        MAIN_EVENT_ACTIVATION_DONE |
        MAIN_EVENT_STATE_CHANGED;

    while (true) {
        auto bits = xEventGroupWaitBits(event_group_, ALL_EVENTS, pdTRUE, pdFALSE, portMAX_DELAY);

        if (bits & MAIN_EVENT_ERROR) {
            SetDeviceState(kDeviceStateIdle);
            Alert(Lang::Strings::ERROR, last_error_message_.c_str(), "circle_xmark", Lang::Sounds::OGG_EXCLAMATION);
        }

        if (bits & MAIN_EVENT_NETWORK_CONNECTED) {
            HandleNetworkConnectedEvent();
        }

        if (bits & MAIN_EVENT_NETWORK_DISCONNECTED) {
            HandleNetworkDisconnectedEvent();
        }

        if (bits & MAIN_EVENT_ACTIVATION_DONE) {
            HandleActivationDoneEvent();
        }

        if (bits & MAIN_EVENT_STATE_CHANGED) {
            HandleStateChangedEvent();
        }

        if (bits & MAIN_EVENT_TOGGLE_CHAT) {
            HandleToggleChatEvent();
        }

        if (bits & MAIN_EVENT_START_LISTENING) {
            HandleStartListeningEvent();
        }

        if (bits & MAIN_EVENT_CANCEL_LISTENING) {
            HandleCancelListeningEvent();
        }

        if (bits & MAIN_EVENT_STOP_LISTENING) {
            HandleStopListeningEvent();
        }

        if (bits & MAIN_EVENT_SEND_AUDIO) {
            while (auto packet = audio_service_.PopPacketFromSendQueue()) {
                if (protocol_ && !protocol_->SendAudio(std::move(packet))) {
                    break;
                }
            }
        }

        if (bits & MAIN_EVENT_WAKE_WORD_DETECTED) {
            HandleWakeWordDetectedEvent();
        }

        if (bits & MAIN_EVENT_VAD_CHANGE) {
            if (GetDeviceState() == kDeviceStateListening) {
                auto led = Board::GetInstance().GetLed();
                led->OnStateChanged();
            }
        }

        if (bits & MAIN_EVENT_SCHEDULE) {
            std::unique_lock<std::mutex> lock(mutex_);
            auto tasks = std::move(main_tasks_);
            lock.unlock();
            for (auto& task : tasks) {
                task();
            }
        }

        if (bits & MAIN_EVENT_CLOCK_TICK) {
            clock_ticks_++;
            auto display = Board::GetInstance().GetDisplay();
            display->UpdateStatusBar();
        
            // Print debug info every 10 seconds
            if (clock_ticks_ % 10 == 0) {
                SystemInfo::PrintHeapStats();
            }
        }
    }
}

void Application::HandleNetworkConnectedEvent() {
    ESP_LOGI(TAG, "Network connected");
    auto state = GetDeviceState();

    if (state == kDeviceStateStarting || state == kDeviceStateWifiConfiguring) {
        // Network is ready, start activation
        SetDeviceState(kDeviceStateActivating);
        if (activation_task_handle_ != nullptr) {
            ESP_LOGW(TAG, "Activation task already running");
            return;
        }

        xTaskCreate([](void* arg) {
            Application* app = static_cast<Application*>(arg);
            app->ActivationTask();
            app->activation_task_handle_ = nullptr;
            vTaskDelete(NULL);
        }, "activation", 4096 * 2, this, 2, &activation_task_handle_);
    }

    // Update the status bar immediately to show the network state
    auto display = Board::GetInstance().GetDisplay();
    display->UpdateStatusBar(true);
}

void Application::HandleNetworkDisconnectedEvent() {
    // Close current conversation when network disconnected
    auto state = GetDeviceState();
    if (state == kDeviceStateConnecting || state == kDeviceStateListening || state == kDeviceStateSpeaking) {
        ESP_LOGI(TAG, "Closing audio channel due to network disconnection");
        protocol_->CloseAudioChannel();
    }

    // Update the status bar immediately to show the network state
    auto display = Board::GetInstance().GetDisplay();
    display->UpdateStatusBar(true);
}

void Application::HandleActivationDoneEvent() {
    ESP_LOGI(TAG, "Activation done");

    SystemInfo::PrintHeapStats();
    SetDeviceState(kDeviceStateIdle);

    has_server_time_ = ota_->HasServerTime();

    auto display = Board::GetInstance().GetDisplay();
    std::string message = std::string(Lang::Strings::VERSION) + ota_->GetCurrentVersion();
    display->ShowNotification(message.c_str());
    display->SetChatMessage("system", "");

    // Release OTA object after activation is complete
    ota_.reset();
    auto& board = Board::GetInstance();
    board.SetPowerSaveLevel(PowerSaveLevel::LOW_POWER);

    Schedule([this]() {
        // Play the success sound to indicate the device is ready
        audio_service_.PlaySound(Lang::Sounds::OGG_SUCCESS);
    });
}

void Application::ActivationTask() {
    // Create OTA object for activation process
    ota_ = std::make_unique<Ota>();

    // Check the release manifest before applying partition-backed assets. This
    // avoids leaving display theme pointers into an assets mapping that an
    // in-place assets update must unmap and replace.
    CheckNewVersion();
    CheckAssetsVersion();

    // Initialize the protocol
    InitializeProtocol();

    // Signal completion to main loop
    xEventGroupSetBits(event_group_, MAIN_EVENT_ACTIVATION_DONE);
}

void Application::CheckAssetsVersion() {
    // Only allow CheckAssetsVersion to be called once
    if (assets_version_checked_) {
        return;
    }
    assets_version_checked_ = true;

    auto& board = Board::GetInstance();
    auto display = board.GetDisplay();
    auto& assets = Assets::GetInstance();

    if (!assets.has_partition()) {
        ESP_LOGW(TAG, "Assets partition is unavailable for board %s", BOARD_NAME);
        return;
    }
    
    Settings settings("assets", true);
    // Check if there is a new assets need to be downloaded
    std::string download_url = settings.GetString("download_url");

    if (!download_url.empty()) {
        char message[256];
        snprintf(message, sizeof(message), Lang::Strings::FOUND_NEW_ASSETS, download_url.c_str());
        Alert(Lang::Strings::LOADING_ASSETS, message, "cloud_arrow_down", Lang::Sounds::OGG_UPGRADE);
        
        // Wait for the audio service to be idle for 3 seconds
        vTaskDelay(pdMS_TO_TICKS(3000));
        SetDeviceState(kDeviceStateUpgrading);
        board.SetPowerSaveLevel(PowerSaveLevel::PERFORMANCE);
        display->SetChatMessage("system", Lang::Strings::PLEASE_WAIT);

        bool success = assets.Download(download_url, [this, display](int progress, size_t speed) -> void {
            char buffer[32];
            snprintf(buffer, sizeof(buffer), "%d%% %uKB/s", progress, speed / 1024);
            Schedule([display, message = std::string(buffer)]() {
                display->SetChatMessage("system", message.c_str());
            });
        });

        board.SetPowerSaveLevel(PowerSaveLevel::LOW_POWER);
        vTaskDelay(pdMS_TO_TICKS(1000));

        if (!success) {
            Alert(Lang::Strings::ERROR, Lang::Strings::DOWNLOAD_ASSETS_FAILED, "circle_xmark", Lang::Sounds::OGG_EXCLAMATION);
            vTaskDelay(pdMS_TO_TICKS(2000));
            SetDeviceState(kDeviceStateActivating);
            return;
        }
        settings.EraseKey("download_url");
    }

    // Apply assets only after manifest-driven updates have finished.
    if (assets.Apply()) {
        board.OnAssetsUpdated();
    }
    display->SetChatMessage("system", "");
    display->SetEmotion("microchip_ai");
}

void Application::CheckNewVersion() {
    auto policy = Ota::GetPolicyStatus();
    if (!policy.automatic_updates_enabled) {
        ESP_LOGW(
            TAG,
            "Automatic OTA is disabled%s%s",
            policy.failed_version.empty() ? "" : " after rollback of ",
            policy.failed_version.c_str());
        return;
    }

    if (ota_->GetCheckVersionUrl().empty()) {
        ESP_LOGI(TAG, "XC Body OTA is disabled: no manifest URL configured");
        return;
    }

    Board::GetInstance().GetDisplay()->SetStatus(
        Lang::Strings::CHECKING_NEW_VERSION);
    esp_err_t err = ota_->CheckVersion();
    if (err != ESP_OK) {
        ESP_LOGW(
            TAG,
            "XC Body OTA check failed, continuing startup: %s",
            esp_err_to_name(err));
        return;
    }

    if (ota_->HasNewVersion()) {
        UpgradeFirmware(
            ota_->GetFirmwareUrl(),
            ota_->GetFirmwareVersion(),
            ota_->GetFirmwareSha256(),
            ota_->GetFirmwareSize());
        return;
    }

    if (!ota_->HasAssetsForCurrentVersion()) {
        return;
    }

    auto& assets = Assets::GetInstance();
    const std::string& sha256 = ota_->GetAssetsSha256();
    size_t size = ota_->GetAssetsSize();
    if (assets.Verify(sha256, size)) {
        return;
    }

    bool expected = false;
    if (!firmware_upgrade_in_progress_.compare_exchange_strong(
            expected, true)) {
        ESP_LOGW(TAG, "Another XC Body update is already in progress");
        return;
    }

    auto& board = Board::GetInstance();
    auto display = board.GetDisplay();
    Alert(
        Lang::Strings::OTA_UPGRADE,
        Lang::Strings::LOADING_ASSETS,
        "cloud_arrow_down",
        Lang::Sounds::OGG_UPGRADE);
    SetDeviceState(kDeviceStateUpgrading);
    board.SetPowerSaveLevel(PowerSaveLevel::PERFORMANCE);
    bool success = assets.DownloadVerified(
        ota_->GetAssetsUrl(),
        sha256,
        size,
        [this, display](int progress, size_t speed) {
            char buffer[32];
            snprintf(
                buffer,
                sizeof(buffer),
                "%d%% %uKB/s",
                progress,
                speed / 1024);
            // OTA itself runs on the application task. Scheduling this back
            // to that same blocked task leaves the screen frozen at 0%.
            display->SetChatMessage("system", buffer);
        });
    firmware_upgrade_in_progress_.store(false);
    board.SetPowerSaveLevel(PowerSaveLevel::LOW_POWER);
    if (!success) {
        ESP_LOGW(
            TAG,
            "Assets update failed; keeping retry metadata and continuing "
            "with static fallback");
        SetDeviceState(kDeviceStateActivating);
        return;
    }

    ESP_LOGI(TAG, "Assets update verified; applying after update checks");
}

void Application::InitializeProtocol() {
    auto& board = Board::GetInstance();
    auto display = board.GetDisplay();
    auto codec = board.GetAudioCodec();

    display->SetStatus(Lang::Strings::LOADING_PROTOCOL);

    // Force WebSocket protocol for the stackchan-mcp gateway (bypass OTA config)
    protocol_ = std::make_unique<WebsocketProtocol>();

    protocol_->OnConnected([this]() {
        DismissAlert();
    });

    protocol_->OnNetworkError([this](const std::string& message) {
        last_error_message_ = message;
        xEventGroupSetBits(event_group_, MAIN_EVENT_ERROR);
    });
    
    protocol_->OnIncomingAudio([this, &board](
            std::unique_ptr<AudioStreamPacket> packet) {
        if (audio_service_.IsPreparedAudioPending()) {
            audio_service_.PushPacketToDecodeQueue(std::move(packet));
        } else if (GetDeviceState() == kDeviceStateSpeaking) {
            audio_service_.PushPacketToDecodeQueue(std::move(packet));
        }
    });
    
    protocol_->OnAudioChannelOpened([this, codec, &board]() {
        board.SetPowerSaveLevel(PowerSaveLevel::PERFORMANCE);
        if (protocol_->server_sample_rate() != codec->output_sample_rate()) {
            ESP_LOGW(TAG, "Server sample rate %d does not match device output sample rate %d, resampling may cause distortion",
                protocol_->server_sample_rate(), codec->output_sample_rate());
        }
    });
    
    protocol_->OnAudioChannelClosed([this, &board]() {
        board.SetPowerSaveLevel(PowerSaveLevel::LOW_POWER);
        std::string closing_transfer_id;
        {
            std::lock_guard<std::mutex> transfer_lock(
                prepared_audio_transfer_mutex_);
            closing_transfer_id = prepared_audio_transfer_id_;
        }
        Schedule([this,
                  closing_transfer_id = std::move(closing_transfer_id)]() {
            std::lock_guard<std::mutex> transfer_lock(
                prepared_audio_transfer_mutex_);
            if (closing_transfer_id != prepared_audio_transfer_id_) {
                return;
            }
            prepared_audio_transfer_id_.clear();
            audio_service_.AbortPreparedAudio();
            auto display = Board::GetInstance().GetDisplay();
            display->SetChatMessage("system", "");
            SetDeviceState(kDeviceStateIdle);
        });
    });
    
    protocol_->OnIncomingJson([this, display, &board](const cJSON* root) {
        // Parse JSON data
        auto type = cJSON_GetObjectItem(root, "type");
        if (strcmp(type->valuestring, "tts") == 0) {
            auto state = cJSON_GetObjectItem(root, "state");
            if (strcmp(state->valuestring, "prepare") == 0) {
                auto count = cJSON_GetObjectItem(root, "packet_count");
                auto transfer_id = cJSON_GetObjectItem(root, "transfer_id");
                if (!cJSON_IsNumber(count) || count->valuedouble <= 0) {
                    ESP_LOGE(TAG, "Invalid prepared audio transfer");
                    return;
                }
                size_t packet_count = static_cast<size_t>(count->valuedouble);
                {
                    std::lock_guard<std::mutex> transfer_lock(
                        prepared_audio_transfer_mutex_);
                    if (!audio_service_.BeginPreparedAudio(packet_count)) {
                        ESP_LOGE(TAG, "Invalid prepared audio transfer");
                        return;
                    }
                    prepared_audio_transfer_id_ =
                        cJSON_IsString(transfer_id) ?
                            transfer_id->valuestring : "";
                }
                Schedule([this]() {
                    aborted_ = false;
                    SetDeviceState(kDeviceStateSpeaking);
                });
            } else if (strcmp(state->valuestring, "play") == 0) {
                Schedule([this, &board]() {
                    bool defer_playback = board.IsTouchReactionActive();
                    if (!audio_service_.CommitPreparedAudio(defer_playback)) {
                        ESP_LOGE(TAG, "Prepared audio transfer incomplete");
                        SetDeviceState(kDeviceStateIdle);
                        return;
                    }
                    if (!defer_playback) {
                        board.OnTtsStart();
                    } else if (!board.IsTouchReactionActive()) {
                        ResumePreparedAudioPlayback();
                    }
                });
            } else if (strcmp(state->valuestring, "start") == 0) {
                Schedule([this, &board]() {
                    aborted_ = false;
                    SetDeviceState(kDeviceStateSpeaking);
                    // Phase 4 audio (Issue #76): drive avatar mouth animation
                    // for the lifetime of this TTS utterance. Default no-op
                    // for boards without a mouth display.
                    board.OnTtsStart();
                });
            } else if (strcmp(state->valuestring, "stop") == 0) {
                auto transfer_id = cJSON_GetObjectItem(root, "transfer_id");
                std::string requested_transfer_id =
                    cJSON_IsString(transfer_id) ? transfer_id->valuestring : "";
                if (requested_transfer_id.empty()) {
                    std::lock_guard<std::mutex> transfer_lock(
                        prepared_audio_transfer_mutex_);
                    requested_transfer_id = prepared_audio_transfer_id_;
                }
                Schedule([this, &board,
                          requested_transfer_id =
                              std::move(requested_transfer_id)]() {
                    std::lock_guard<std::mutex> transfer_lock(
                        prepared_audio_transfer_mutex_);
                    if (requested_transfer_id !=
                            prepared_audio_transfer_id_) {
                        return;
                    }
                    if (audio_service_.IsPreparedAudioPending()) {
                        audio_service_.AbortPreparedAudio();
                    }
                    bool drained = audio_service_.WaitForPlaybackQueueEmpty(
                        kPlaybackDrainTimeout);
                    if (!drained) {
                        ESP_LOGW(TAG, "Audio drain timed out; dropping queue");
                    }
                    auto metrics = audio_service_.GetPreparedAudioMetrics();
                    if (!prepared_audio_transfer_id_.empty()) {
                        cJSON* result = cJSON_CreateObject();
                        cJSON_AddStringToObject(
                            result, "type", "prepared_audio_metrics");
                        cJSON_AddStringToObject(
                            result, "transfer_id",
                            prepared_audio_transfer_id_.c_str());
                        cJSON_AddNumberToObject(
                            result, "received_packets",
                            static_cast<double>(metrics.received_packets));
                        cJSON_AddNumberToObject(
                            result, "decoded_packets",
                            static_cast<double>(metrics.decoded_packets));
                        cJSON_AddNumberToObject(
                            result, "output_frames",
                            static_cast<double>(metrics.output_frames));
                        cJSON_AddNumberToObject(
                            result, "peak_amplitude",
                            metrics.peak_amplitude);
                        cJSON_AddBoolToObject(
                            result, "output_failed",
                            metrics.output_failed);
                        cJSON_AddBoolToObject(
                            result, "deferred", metrics.deferred);
                        cJSON_AddBoolToObject(
                            result, "drain_timed_out", !drained);
                        char* result_str = cJSON_PrintUnformatted(result);
                        if (result_str != nullptr && protocol_) {
                            protocol_->SendText(std::string(result_str));
                            cJSON_free(result_str);
                        }
                        cJSON_Delete(result);
                    }
                    audio_service_.AbortPreparedAudio();
                    prepared_audio_transfer_id_.clear();
                    if (GetDeviceState() == kDeviceStateSpeaking) {
                        // stackchan-mcp is an MCP gateway, not a standalone
                        // xiaozhi-style conversational agent. Listening must
                        // be triggered explicitly — either by the user
                        // (touch / button / external command) or by the
                        // AI (gateway-issued StartListening). The upstream
                        // xiaozhi behaviour of automatically re-entering
                        // listening after every TTS utterance is a footgun
                        // here: when the firmware's TTS pipeline stalls
                        // (e.g. audio_input task watchdog timeouts during
                        // long playback) the deferred tts.stop event lands
                        // long after the user expected the conversation to
                        // be quiescent, and the device then records ~30 s
                        // of ambient room audio that the gateway happily
                        // posts as a user utterance.
                        //
                        // Returning to Idle here forces the listening
                        // boundary to be set explicitly by whoever wants
                        // to continue the conversation (user touch,
                        // gateway-driven push-to-talk, etc.). Loop-style
                        // Listening→Speaking→Listening flows belong on
                        // the gateway side, not in this firmware.
                        SetDeviceState(kDeviceStateIdle);
                        board.SetPowerSaveLevel(PowerSaveLevel::LOW_POWER);
                    }
                    // Phase 4 audio (Issue #76): stop the avatar mouth
                    // animation unconditionally on tts.stop. A wake-word /
                    // button interrupt can call AbortSpeaking() and move
                    // the device out of Speaking before the server's
                    // tts.stop arrives, in which case the previous-state
                    // guard above is false but the audio playback has
                    // ended and the mouth animation must still stop.
                    // OnTtsStop() is idempotent (no-op for boards without
                    // an avatar / when lip-sync is already stopped).
                    board.OnTtsStop();
                });
            } else if (strcmp(state->valuestring, "sentence_start") == 0) {
                auto text = cJSON_GetObjectItem(root, "text");
                if (cJSON_IsString(text)) {
                    ESP_LOGI(TAG, "<< %s", text->valuestring);
                    Schedule([display, message = std::string(text->valuestring)]() {
                        display->SetChatMessage("assistant", message.c_str());
                    });
                }
            }
        } else if (strcmp(type->valuestring, "listen") == 0) {
            // Server-driven listening trigger (Issue #91,
            // kisaragi-mochi/stackchan-mcp). Mirrors the existing
            // device->gateway ``Protocol::SendStartListening()`` wire format in
            // the reverse direction so the gateway can request the device to
            // enter / leave listening state without a physical button press or
            // wake-word. Used by the gateway-side ``listen()`` MCP tool to
            // perform STT capture on demand.
            //
            // ``profile`` selects the microphone capture source for this
            // listen session. Missing / ``voice`` preserves the existing AFE
            // voice path; ``raw`` bypasses AFE and streams pre-AFE mic
            // PCM through the same Opus SendAudio path.
            //
            // Phase 1 honours only ``state: "start" | "stop"``; the ``mode``
            // field is parsed but currently ignored because
            // ``HandleStartListeningEvent()`` unconditionally calls
            // ``SetListeningMode(kListeningModeManualStop)``. Manual-stop is
            // also the right default for gateway-driven capture: the gateway
            // controls the exact stop boundary by issuing
            // ``{"type":"listen","state":"stop"}`` after its capture window.
            // Threading ``auto`` / ``realtime`` mode through is a follow-up.
            auto state = cJSON_GetObjectItem(root, "state");
            if (!cJSON_IsString(state)) {
                ESP_LOGW(TAG, "listen message missing state");
            } else if (strcmp(state->valuestring, "start") == 0) {
                auto profile = ParseListenProfile(root);
                Schedule([this, profile]() {
                    StartListening(profile);
                });
            } else if (strcmp(state->valuestring, "stop") == 0) {
                StopListening();
            } else {
                ESP_LOGW(TAG, "Unknown listen state: %s", state->valuestring);
            }
        } else if (strcmp(type->valuestring, "stt") == 0) {
            auto text = cJSON_GetObjectItem(root, "text");
            if (cJSON_IsString(text)) {
                ESP_LOGI(TAG, ">> %s", text->valuestring);
                Schedule([display, message = std::string(text->valuestring)]() {
                    display->SetChatMessage("user", message.c_str());
                });
            }
        } else if (strcmp(type->valuestring, "llm") == 0) {
            auto emotion = cJSON_GetObjectItem(root, "emotion");
            if (cJSON_IsString(emotion)) {
                Schedule([display, emotion_str = std::string(emotion->valuestring)]() {
                    display->SetEmotion(emotion_str.c_str());
                });
            }
        } else if (strcmp(type->valuestring, "mcp") == 0) {
            auto payload = cJSON_GetObjectItem(root, "payload");
            if (cJSON_IsObject(payload)) {
                McpServer::GetInstance().ParseMessage(payload);
            }
        } else if (strcmp(type->valuestring, "system") == 0) {
            auto command = cJSON_GetObjectItem(root, "command");
            if (cJSON_IsString(command)) {
                ESP_LOGI(TAG, "System command: %s", command->valuestring);
                if (strcmp(command->valuestring, "reboot") == 0) {
                    // Do a reboot if user requests a OTA update
                    Schedule([this]() {
                        Reboot();
                    });
                } else {
                    ESP_LOGW(TAG, "Unknown system command: %s", command->valuestring);
                }
            }
        } else if (strcmp(type->valuestring, "alert") == 0) {
            auto status = cJSON_GetObjectItem(root, "status");
            auto message = cJSON_GetObjectItem(root, "message");
            auto emotion = cJSON_GetObjectItem(root, "emotion");
            if (cJSON_IsString(status) && cJSON_IsString(message) && cJSON_IsString(emotion)) {
                Alert(status->valuestring, message->valuestring, emotion->valuestring, Lang::Sounds::OGG_VIBRATION);
            } else {
                ESP_LOGW(TAG, "Alert command requires status, message and emotion");
            }
        } else if (strcmp(type->valuestring, "avatar_set_fetch") == 0) {
            // Phase 4.5 avatar (saiverse-stackchan-addon): dispatch to the
            // current board for HTTP fetch + SHA256 verify + AvatarSet adoption.
            // Non-stackchan boards default to a no-op (Board::OnAvatarSetFetch).
            // See docs/intent/stackchan_avatar_pipeline.md §C-3 (SAIVerse).
            board.OnAvatarSetFetch(root);
#if CONFIG_RECEIVE_CUSTOM_MESSAGE
        } else if (strcmp(type->valuestring, "custom") == 0) {
            auto payload = cJSON_GetObjectItem(root, "payload");
            ESP_LOGI(TAG, "Received custom message: %s", cJSON_PrintUnformatted(root));
            if (cJSON_IsObject(payload)) {
                Schedule([this, display, payload_str = std::string(cJSON_PrintUnformatted(payload))]() {
                    display->SetChatMessage("system", payload_str.c_str());
                });
            } else {
                ESP_LOGW(TAG, "Invalid custom message format: missing payload");
            }
#endif
        } else {
            ESP_LOGW(TAG, "Unknown message type: %s", type->valuestring);
        }
    });
    
    protocol_->Start();
}

void Application::ShowActivationCode(const std::string& code, const std::string& message) {
    struct digit_sound {
        char digit;
        const std::string_view& sound;
    };
    static const std::array<digit_sound, 10> digit_sounds{{
        digit_sound{'0', Lang::Sounds::OGG_0},
        digit_sound{'1', Lang::Sounds::OGG_1}, 
        digit_sound{'2', Lang::Sounds::OGG_2},
        digit_sound{'3', Lang::Sounds::OGG_3},
        digit_sound{'4', Lang::Sounds::OGG_4},
        digit_sound{'5', Lang::Sounds::OGG_5},
        digit_sound{'6', Lang::Sounds::OGG_6},
        digit_sound{'7', Lang::Sounds::OGG_7},
        digit_sound{'8', Lang::Sounds::OGG_8},
        digit_sound{'9', Lang::Sounds::OGG_9}
    }};

    // This sentence uses 9KB of SRAM, so we need to wait for it to finish
    Alert(Lang::Strings::ACTIVATION, message.c_str(), "link", Lang::Sounds::OGG_ACTIVATION);

    for (const auto& digit : code) {
        auto it = std::find_if(digit_sounds.begin(), digit_sounds.end(),
            [digit](const digit_sound& ds) { return ds.digit == digit; });
        if (it != digit_sounds.end()) {
            audio_service_.PlaySound(it->sound);
        }
    }
}

void Application::Alert(const char* status, const char* message, const char* emotion, const std::string_view& sound) {
    ESP_LOGW(TAG, "Alert [%s] %s: %s", emotion, status, message);
    auto display = Board::GetInstance().GetDisplay();
    display->SetStatus(status);
    display->SetEmotion(emotion);
    display->SetChatMessage("system", message);
    if (!sound.empty()) {
        audio_service_.PlaySound(sound);
    }
}

void Application::DismissAlert() {
    if (GetDeviceState() == kDeviceStateIdle) {
        auto display = Board::GetInstance().GetDisplay();
        display->SetStatus(Lang::Strings::STANDBY);
        display->SetEmotion("neutral");
        display->SetChatMessage("system", "");
    }
}

void Application::ToggleChatState() {
    xEventGroupSetBits(event_group_, MAIN_EVENT_TOGGLE_CHAT);
}

uint32_t Application::BeginListeningRequest(ListeningProfile profile) {
    uint32_t generation = listening_request_generation_.fetch_add(1, std::memory_order_acq_rel) + 1;
    pending_listening_profile_.store(profile, std::memory_order_release);
    pending_listening_generation_.store(generation, std::memory_order_release);
    return generation;
}

void Application::InvalidatePendingListeningRequest() {
    listening_request_generation_.fetch_add(1, std::memory_order_acq_rel);
    pending_listening_profile_.store(kListeningProfileVoice, std::memory_order_release);
    pending_listening_generation_.store(0, std::memory_order_release);
}

bool Application::IsListeningRequestCurrent(uint32_t generation) const {
    return generation != 0 &&
        listening_request_generation_.load(std::memory_order_acquire) == generation;
}

void Application::StartListening(ListeningProfile profile) {
    // Thin event setter. The popup-on-listening flag is armed inside
    // HandleStartListeningEvent (main task) so all writes to
    // play_popup_on_listening_ converge to the same task that reads
    // and clears it in HandleStateChangedEvent.
    BeginListeningRequest(profile);
    xEventGroupSetBits(event_group_, MAIN_EVENT_START_LISTENING);
}

void Application::StopListening() {
    InvalidatePendingListeningRequest();
    xEventGroupSetBits(event_group_, MAIN_EVENT_STOP_LISTENING);
}

void Application::CancelListening() {
    InvalidatePendingListeningRequest();
    xEventGroupSetBits(event_group_, MAIN_EVENT_CANCEL_LISTENING);
}

void Application::HandleToggleChatEvent() {
    auto state = GetDeviceState();
    
    if (state == kDeviceStateActivating) {
        SetDeviceState(kDeviceStateIdle);
        return;
    } else if (state == kDeviceStateWifiConfiguring) {
        audio_service_.EnableAudioTesting(true);
        SetDeviceState(kDeviceStateAudioTesting);
        return;
    } else if (state == kDeviceStateAudioTesting) {
        audio_service_.EnableAudioTesting(false);
        SetDeviceState(kDeviceStateWifiConfiguring);
        return;
    }

    if (!protocol_) {
        ESP_LOGE(TAG, "Protocol not initialized");
        return;
    }

    if (state == kDeviceStateIdle) {
        ListeningMode mode = GetDefaultListeningMode();
        if (!protocol_->IsAudioChannelOpened()) {
            uint32_t generation = BeginListeningRequest(kListeningProfileVoice);
            SetDeviceState(kDeviceStateConnecting);
            // Schedule to let the state change be processed first (UI update)
            Schedule([this, mode, generation]() {
                ContinueOpenAudioChannel(mode, generation);
            });
            return;
        }
        SetListeningMode(mode);
    } else if (state == kDeviceStateSpeaking) {
        AbortSpeaking(kAbortReasonNone);
    } else if (state == kDeviceStateListening) {
        protocol_->CloseAudioChannel();
    }
}

void Application::ContinueOpenAudioChannel(ListeningMode mode, uint32_t generation) {
    // Check state again in case it was changed during scheduling
    if (GetDeviceState() != kDeviceStateConnecting || !IsListeningRequestCurrent(generation)) {
        listening_profile_ = ListeningProfileAfterStop(listening_profile_);
        return;
    }

    if (!protocol_->IsAudioChannelOpened()) {
        if (!protocol_->OpenAudioChannel()) {
            return;
        }
    }

    if (!IsListeningRequestCurrent(generation)) {
        if (protocol_->IsAudioChannelOpened()) {
            protocol_->CloseAudioChannel();
        }
        listening_profile_ = ListeningProfileAfterStop(listening_profile_);
        SetDeviceState(kDeviceStateIdle);
        return;
    }

    SetListeningMode(mode);
}

void Application::HandleStartListeningEvent() {
    auto state = GetDeviceState();
    auto requested_generation = pending_listening_generation_.load(std::memory_order_acquire);
    if (!IsListeningRequestCurrent(requested_generation)) {
        return;
    }
    
    if (state == kDeviceStateActivating) {
        SetDeviceState(kDeviceStateIdle);
        return;
    } else if (state == kDeviceStateWifiConfiguring) {
        audio_service_.EnableAudioTesting(true);
        SetDeviceState(kDeviceStateAudioTesting);
        return;
    }

    if (!protocol_) {
        ESP_LOGE(TAG, "Protocol not initialized");
        return;
    }

    auto requested_profile = pending_listening_profile_.load(std::memory_order_acquire);
    if (state == kDeviceStateIdle || state == kDeviceStateSpeaking) {
        listening_profile_ = requested_profile;

        // Arm the OGG_POPUP cue that HandleStateChangedEvent plays after
        // the kDeviceStateListening branch resets the decoder
        // (~line 980). Previously this flag was set only on wake-word
        // activation paths (HandleWakeWordDetectedEvent /
        // ContinueWakeWordInvoke), so callers of the public
        // StartListening() API — board-level touch buttons,
        // server-driven listen, etc. — silently lost the cue.
        //
        // Setting it here (main task, after the Activating /
        // WifiConfiguring / null-protocol early returns and gated on
        // the states that actually transition toward Listening) avoids
        // latching the flag for a no-op StartListening so an unrelated
        // future Listening transition doesn't unexpectedly play the
        // popup.
        play_popup_on_listening_ = true;
    }

    if (state == kDeviceStateIdle) {
        if (!protocol_->IsAudioChannelOpened()) {
            SetDeviceState(kDeviceStateConnecting);
            // Schedule to let the state change be processed first (UI update)
            Schedule([this, requested_generation]() {
                ContinueOpenAudioChannel(kListeningModeManualStop, requested_generation);
            });
            return;
        }
        SetListeningMode(kListeningModeManualStop);
    } else if (state == kDeviceStateSpeaking) {
        AbortSpeaking(kAbortReasonNone);
        SetListeningMode(kListeningModeManualStop);
    }
}

void Application::HandleStopListeningEvent() {
    auto state = GetDeviceState();
    
    if (state == kDeviceStateAudioTesting) {
        audio_service_.EnableAudioTesting(false);
        SetDeviceState(kDeviceStateWifiConfiguring);
        return;
    } else if (state == kDeviceStateConnecting) {
        listening_profile_ = ListeningProfileAfterStop(listening_profile_);
        play_popup_on_listening_ = false;
        if (protocol_ && protocol_->IsAudioChannelOpened()) {
            protocol_->CloseAudioChannel();
        }
        SetDeviceState(kDeviceStateIdle);
        return;
    } else if (state == kDeviceStateListening) {
        if (protocol_) {
            protocol_->SendStopListening();
        }
        SetDeviceState(kDeviceStateIdle);
    }
}

void Application::HandleCancelListeningEvent() {
    auto state = GetDeviceState();

    if (state == kDeviceStateConnecting) {
        listening_profile_ = ListeningProfileAfterStop(listening_profile_);
        play_popup_on_listening_ = false;
        if (protocol_ && protocol_->IsAudioChannelOpened()) {
            protocol_->SendCancelListening();
        }
        SetDeviceState(kDeviceStateIdle);
    } else if (state == kDeviceStateListening) {
        if (protocol_) {
            protocol_->SendCancelListening();
        }
        SetDeviceState(kDeviceStateIdle);
    }
}

void Application::HandleWakeWordDetectedEvent() {
    if (!protocol_) {
        return;
    }

    auto state = GetDeviceState();
    if (listening_profile_ == kListeningProfileRaw) {
        ESP_LOGI(TAG, "Ignoring wake word event while raw listening profile is active (state: %d)", (int)state);
        audio_service_.EnableWakeWordDetection(false);
        return;
    }

    auto wake_word = audio_service_.GetLastWakeWord();
    ESP_LOGI(TAG, "Wake word detected: %s (state: %d)", wake_word.c_str(), (int)state);

    if (state == kDeviceStateIdle) {
        audio_service_.EncodeWakeWord();
        auto wake_word = audio_service_.GetLastWakeWord();
        uint32_t generation = BeginListeningRequest(kListeningProfileVoice);

        if (!protocol_->IsAudioChannelOpened()) {
            SetDeviceState(kDeviceStateConnecting);
            // Schedule to let the state change be processed first (UI update),
            // then continue with OpenAudioChannel which may block for ~1 second
            Schedule([this, wake_word, generation]() {
                ContinueWakeWordInvoke(wake_word, generation);
            });
            return;
        }
        // Channel already opened, continue directly
        ContinueWakeWordInvoke(wake_word, generation);
    } else if (state == kDeviceStateSpeaking || state == kDeviceStateListening) {
        AbortSpeaking(kAbortReasonWakeWordDetected);
        // Clear send queue to avoid sending residues to server
        while (audio_service_.PopPacketFromSendQueue());

        if (state == kDeviceStateListening) {
            protocol_->SendStartListening(GetDefaultListeningMode());
            audio_service_.ResetDecoder();
            audio_service_.PlaySound(Lang::Sounds::OGG_POPUP);
            // Re-enable wake word detection as it was stopped by the detection itself
            audio_service_.EnableWakeWordDetection(true);
        } else {
            // Play popup sound and start listening again
            play_popup_on_listening_ = true;
            SetListeningMode(GetDefaultListeningMode());
        }
    } else if (state == kDeviceStateActivating) {
        // Restart the activation check if the wake word is detected during activation
        SetDeviceState(kDeviceStateIdle);
    }
}

void Application::ContinueWakeWordInvoke(const std::string& wake_word, uint32_t generation) {
    // Check state again in case it was changed during scheduling
    if (GetDeviceState() != kDeviceStateConnecting || !IsListeningRequestCurrent(generation)) {
        listening_profile_ = ListeningProfileAfterStop(listening_profile_);
        return;
    }

    if (!protocol_->IsAudioChannelOpened()) {
        if (!protocol_->OpenAudioChannel()) {
            audio_service_.EnableWakeWordDetection(true);
            return;
        }
    }

    if (!IsListeningRequestCurrent(generation)) {
        if (protocol_->IsAudioChannelOpened()) {
            protocol_->CloseAudioChannel();
        }
        listening_profile_ = ListeningProfileAfterStop(listening_profile_);
        SetDeviceState(kDeviceStateIdle);
        return;
    }

    ESP_LOGI(TAG, "Wake word detected: %s", wake_word.c_str());
#if CONFIG_SEND_WAKE_WORD_DATA
    // Encode and send the wake word data to the server
    while (auto packet = audio_service_.PopWakeWordPacket()) {
        protocol_->SendAudio(std::move(packet));
    }
    // Set the chat state to wake word detected
    protocol_->SendWakeWordDetected(wake_word);
    SetListeningMode(GetDefaultListeningMode());
#else
    // Set flag to play popup sound after state changes to listening
    // (PlaySound here would be cleared by ResetDecoder in EnableVoiceProcessing)
    play_popup_on_listening_ = true;
    SetListeningMode(GetDefaultListeningMode());
#endif
}

void Application::HandleStateChangedEvent() {
    DeviceState new_state = state_machine_.GetState();
    clock_ticks_ = 0;

    auto& board = Board::GetInstance();
    auto display = board.GetDisplay();
    auto led = board.GetLed();
    led->OnStateChanged();
    
    switch (new_state) {
        case kDeviceStateUnknown:
        case kDeviceStateIdle:
            display->SetStatus(Lang::Strings::STANDBY);
            display->ClearChatMessages();  // Clear messages first
            display->SetEmotion("neutral"); // Then set emotion (wechat mode checks child count)
            audio_service_.EnableRawCapture(false);
            audio_service_.EnableVoiceProcessing(false);
            listening_profile_ = ListeningProfileAfterStop(listening_profile_);
            audio_service_.EnableWakeWordDetection(true);
            break;
        case kDeviceStateConnecting:
            display->SetStatus(Lang::Strings::CONNECTING);
            display->SetEmotion("neutral");
            display->SetChatMessage("system", "");
            break;
        case kDeviceStateListening: {
            display->SetStatus(Lang::Strings::LISTENING);
            display->SetEmotion("neutral");

            // Make sure the selected listening source is running
            bool is_raw_profile = listening_profile_ == kListeningProfileRaw;
            bool is_listening_source_running = is_raw_profile
                ? audio_service_.IsRawCaptureRunning()
                : audio_service_.IsAudioProcessorRunning();
            if (play_popup_on_listening_ || !is_listening_source_running) {
                // For auto mode, wait for playback queue to be empty before enabling mic capture
                // This prevents audio truncation when STOP arrives late due to network jitter
                if (listening_mode_ == kListeningModeAutoStop) {
                    if (!audio_service_.WaitForPlaybackQueueEmpty(
                            kPlaybackDrainTimeout)) {
                        ESP_LOGW(TAG, "Audio drain timed out; dropping queue");
                        audio_service_.AbortPreparedAudio();
                    }
                }
                
                // Send the start listening command
                protocol_->SendStartListening(listening_mode_);
                if (is_raw_profile) {
                    audio_service_.EnableVoiceProcessing(false);
                    audio_service_.EnableRawCapture(true);
                } else {
                    audio_service_.EnableRawCapture(false);
                    audio_service_.EnableVoiceProcessing(true);
                }
            }

#ifdef CONFIG_WAKE_WORD_DETECTION_IN_LISTENING
            // Enable wake word detection in listening mode (configured via Kconfig)
            audio_service_.EnableWakeWordDetection(audio_service_.IsAfeWakeWord());
#else
            // Disable wake word detection in listening mode
            audio_service_.EnableWakeWordDetection(false);
#endif
            
            // Play popup sound after ResetDecoder (in EnableVoiceProcessing) has been called
            if (play_popup_on_listening_) {
                play_popup_on_listening_ = false;
                audio_service_.PlaySound(Lang::Sounds::OGG_POPUP);
            }
            break;
        }
        case kDeviceStateSpeaking:
            display->SetStatus(Lang::Strings::SPEAKING);
            board.SetPowerSaveLevel(PowerSaveLevel::PERFORMANCE);

            if (listening_profile_ == kListeningProfileRaw) {
                audio_service_.EnableRawCapture(false);
            }
            if (listening_mode_ != kListeningModeRealtime) {
                audio_service_.EnableVoiceProcessing(false);
                // Only AFE wake word can be detected in speaking mode
                audio_service_.EnableWakeWordDetection(audio_service_.IsAfeWakeWord());
            }
            listening_profile_ = ListeningProfileAfterStop(listening_profile_);
            audio_service_.ResetDecoder();
            break;
        case kDeviceStateWifiConfiguring:
            audio_service_.EnableRawCapture(false);
            audio_service_.EnableVoiceProcessing(false);
            listening_profile_ = ListeningProfileAfterStop(listening_profile_);
            audio_service_.EnableWakeWordDetection(false);
            break;
        default:
            // Do nothing
            break;
    }
    board.OnDeviceStateChanged(new_state);
}

void Application::Schedule(std::function<void()>&& callback) {
    {
        std::lock_guard<std::mutex> lock(mutex_);
        main_tasks_.push_back(std::move(callback));
    }
    xEventGroupSetBits(event_group_, MAIN_EVENT_SCHEDULE);
}

void Application::AbortSpeaking(AbortReason reason) {
    ESP_LOGI(TAG, "Abort speaking");
    aborted_ = true;
    if (protocol_) {
        protocol_->SendAbortSpeaking(reason);
    }
}

void Application::SetListeningMode(ListeningMode mode) {
    listening_mode_ = mode;
    SetDeviceState(kDeviceStateListening);
}

ListeningMode Application::GetDefaultListeningMode() const {
    return aec_mode_ == kAecOff ? kListeningModeAutoStop : kListeningModeRealtime;
}

void Application::Reboot() {
    ESP_LOGI(TAG, "Rebooting...");
    // Disconnect the audio channel
    if (protocol_ && protocol_->IsAudioChannelOpened()) {
        protocol_->CloseAudioChannel();
    }
    protocol_.reset();
    audio_service_.Stop();

    vTaskDelay(pdMS_TO_TICKS(1000));
    esp_restart();
}

bool Application::UpgradeFirmware(
    const std::string& url,
    const std::string& version,
    const std::string& expected_sha256,
    size_t expected_size) {
    bool expected = false;
    if (!firmware_upgrade_in_progress_.compare_exchange_strong(
            expected, true)) {
        ESP_LOGW(TAG, "Firmware upgrade already in progress");
        return false;
    }

    auto& board = Board::GetInstance();
    auto display = board.GetDisplay();
    auto previous_state = GetDeviceState();

    std::string upgrade_url = url;
    std::string version_info = version.empty() ? "(Manual upgrade)" : version;

    // Close audio channel if it's open
    if (protocol_ && protocol_->IsAudioChannelOpened()) {
        ESP_LOGI(TAG, "Closing audio channel before firmware upgrade");
        protocol_->CloseAudioChannel();
    }
    ESP_LOGI(TAG, "Starting firmware upgrade from URL: %s", upgrade_url.c_str());

    Alert(Lang::Strings::OTA_UPGRADE, Lang::Strings::UPGRADING, "download", Lang::Sounds::OGG_UPGRADE);
    vTaskDelay(pdMS_TO_TICKS(3000));

    SetDeviceState(kDeviceStateUpgrading);

    std::string message = std::string(Lang::Strings::NEW_VERSION) + version_info;
    display->SetChatMessage("system", message.c_str());

    board.SetPowerSaveLevel(PowerSaveLevel::PERFORMANCE);
    audio_service_.Stop();
    vTaskDelay(pdMS_TO_TICKS(1000));

    bool upgrade_success = Ota::Upgrade(
        upgrade_url,
        version,
        expected_sha256,
        expected_size,
        [this, display](int progress, size_t speed) {
            char buffer[32];
            snprintf(
                buffer,
                sizeof(buffer),
                "%d%% %uKB/s",
                progress,
                speed / 1024);
            // UpgradeFirmware runs on the application task, so update the
            // display here instead of queuing work behind the download.
            display->SetChatMessage("system", buffer);
        });

    if (!upgrade_success) {
        firmware_upgrade_in_progress_.store(false);
        // Upgrade failed, restart audio service and continue running
        ESP_LOGE(TAG, "Firmware upgrade failed, restarting audio service and continuing operation...");
        audio_service_.Start(); // Restart audio service
        board.SetPowerSaveLevel(PowerSaveLevel::LOW_POWER); // Restore power save level
        Alert(Lang::Strings::ERROR, Lang::Strings::UPGRADE_FAILED, "circle_xmark", Lang::Sounds::OGG_EXCLAMATION);
        vTaskDelay(pdMS_TO_TICKS(3000));
        SetDeviceState(
            previous_state == kDeviceStateActivating
                ? kDeviceStateActivating
                : kDeviceStateIdle);
        return false;
    } else {
        // Upgrade success, reboot immediately
        ESP_LOGI(TAG, "Firmware upgrade successful, rebooting...");
        display->SetChatMessage("system", "Upgrade successful, rebooting...");
        vTaskDelay(pdMS_TO_TICKS(1000)); // Brief pause to show message
        Reboot();
        return true;
    }
}

void Application::WakeWordInvoke(const std::string& wake_word) {
    if (!protocol_) {
        return;
    }

    auto state = GetDeviceState();
    
    if (state == kDeviceStateIdle) {
        audio_service_.EncodeWakeWord();
        uint32_t generation = BeginListeningRequest(kListeningProfileVoice);

        if (!protocol_->IsAudioChannelOpened()) {
            SetDeviceState(kDeviceStateConnecting);
            // Schedule to let the state change be processed first (UI update)
            Schedule([this, wake_word, generation]() {
                ContinueWakeWordInvoke(wake_word, generation);
            });
            return;
        }
        // Channel already opened, continue directly
        ContinueWakeWordInvoke(wake_word, generation);
    } else if (state == kDeviceStateSpeaking) {
        Schedule([this]() {
            AbortSpeaking(kAbortReasonNone);
        });
    } else if (state == kDeviceStateListening) {   
        Schedule([this]() {
            if (protocol_) {
                protocol_->CloseAudioChannel();
            }
        });
    }
}

bool Application::CanEnterSleepMode() {
    if (GetDeviceState() != kDeviceStateIdle) {
        return false;
    }

    if (protocol_ && protocol_->IsAudioChannelOpened()) {
        return false;
    }

    // Most boards block power saving while their control transport is live.
    // A board may opt into a transport-safe display-only idle mode.
    if (protocol_ && protocol_->IsTransportConnected() &&
            !Board::GetInstance().CanPowerSaveWithTransport()) {
        return false;
    }

    if (!audio_service_.IsIdle()) {
        return false;
    }

    // Now it is safe to enter sleep mode
    return true;
}

void Application::SendMcpMessage(const std::string& payload) {
    // Always schedule to run in main task for thread safety
    Schedule([this, payload = std::move(payload)]() {
        if (protocol_) {
            protocol_->SendMcpMessage(payload);
        }
    });
}

void Application::SendStackChanEvent(
    const char* event_type,
    const char* subtype,
    uint64_t duration_ms,
    const char* behavior_id) {
    std::string event_type_str = event_type ? event_type : "";
    std::string subtype_str = subtype ? subtype : "";
    std::string behavior_id_str = behavior_id ? behavior_id : "";
    Schedule([this, event_type_str, subtype_str, duration_ms,
              behavior_id_str]() {
        if (!protocol_ || !protocol_->IsTransportConnected()) {
            return;
        }

        cJSON* root = cJSON_CreateObject();
        if (root == nullptr) {
            return;
        }
        cJSON_AddStringToObject(root, "session_id", protocol_->session_id().c_str());
        cJSON_AddStringToObject(root, "type", "stackchan-event");
        cJSON_AddStringToObject(root, "event_type", event_type_str.c_str());
        cJSON_AddStringToObject(root, "subtype", subtype_str.c_str());
        cJSON_AddNumberToObject(root, "duration_ms", static_cast<double>(duration_ms));
        cJSON_AddNumberToObject(root, "ts", static_cast<double>(esp_timer_get_time() / 1000ULL));
        if (!behavior_id_str.empty()) {
            cJSON_AddStringToObject(
                root, "behavior_id", behavior_id_str.c_str());
        }

        char* str = cJSON_PrintUnformatted(root);
        if (str != nullptr) {
            protocol_->SendText(std::string(str));
            cJSON_free(str);
        }
        cJSON_Delete(root);
    });
}

void Application::SendJsonString(const std::string& json_str) {
    // Thread-safe generic WS text frame send. Used by board-initiated
    // notifications such as avatar_set_loaded (Phase 4.5 avatar). Mirrors
    // SendMcpMessage's main-task Schedule pattern for protocol safety.
    Schedule([this, json_str]() {
        if (protocol_) {
            protocol_->SendText(json_str);
        }
    });
}

void Application::SetAecMode(AecMode mode) {
    aec_mode_ = mode;
    Schedule([this]() {
        auto& board = Board::GetInstance();
        auto display = board.GetDisplay();
        switch (aec_mode_) {
        case kAecOff:
            audio_service_.EnableDeviceAec(false);
            display->ShowNotification(Lang::Strings::RTC_MODE_OFF);
            break;
        case kAecOnServerSide:
            audio_service_.EnableDeviceAec(false);
            display->ShowNotification(Lang::Strings::RTC_MODE_ON);
            break;
        case kAecOnDeviceSide:
            audio_service_.EnableDeviceAec(true);
            display->ShowNotification(Lang::Strings::RTC_MODE_ON);
            break;
        }

        // If the AEC mode is changed, close the audio channel
        if (protocol_ && protocol_->IsAudioChannelOpened()) {
            protocol_->CloseAudioChannel();
        }
    });
}

void Application::PlaySound(const std::string_view& sound) {
    audio_service_.PlaySound(sound);
}

void Application::ResetProtocol() {
    Schedule([this]() {
        // Close audio channel if opened
        if (protocol_ && protocol_->IsAudioChannelOpened()) {
            protocol_->CloseAudioChannel();
        }
        // Reset protocol
        protocol_.reset();
    });
}
