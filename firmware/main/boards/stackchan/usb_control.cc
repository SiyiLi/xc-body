#include "usb_control.h"

#include <cstdio>
#include <cstring>
#include <string>

#include <cJSON.h>
#include <esp_app_desc.h>
#include <esp_log.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <wifi_manager.h>

#include "application.h"
#include "device_state_machine.h"
#include "settings.h"

namespace {

constexpr char kRequestPrefix[] = "XC_BODY_REQUEST ";
constexpr char kResponsePrefix[] = "XC_BODY_RESPONSE ";
constexpr size_t kMaxRequestBytes = 1024;
constexpr uint32_t kTaskStackSize = 6144;
constexpr UBaseType_t kTaskPriority = tskIDLE_PRIORITY + 1;
const char* const kTag = "StackChanUsb";

bool IsString(const cJSON* value) {
    return value != nullptr && cJSON_IsString(value) &&
        value->valuestring != nullptr;
}

bool HasGatewayScheme(const std::string& value) {
    return value.empty() || value.rfind("ws://", 0) == 0 ||
        value.rfind("wss://", 0) == 0;
}

bool IsUrlForced() {
#if defined(CONFIG_FORCE_DEFAULT_WEBSOCKET_URL) && \
    defined(CONFIG_DEFAULT_WEBSOCKET_URL)
    return CONFIG_DEFAULT_WEBSOCKET_URL[0] != '\0';
#else
    return false;
#endif
}

bool IsFallbackUrlForced() {
#if defined(CONFIG_FORCE_DEFAULT_WEBSOCKET_URL) && \
    defined(CONFIG_DEFAULT_WEBSOCKET_FALLBACK_URL)
    return CONFIG_DEFAULT_WEBSOCKET_FALLBACK_URL[0] != '\0';
#else
    return false;
#endif
}

bool IsTokenForced() {
#if defined(CONFIG_FORCE_DEFAULT_WEBSOCKET_URL) && \
    defined(CONFIG_DEFAULT_WEBSOCKET_TOKEN)
    return CONFIG_DEFAULT_WEBSOCKET_TOKEN[0] != '\0';
#else
    return false;
#endif
}

cJSON* NewResponse(const char* command, bool ok) {
    auto response = cJSON_CreateObject();
    cJSON_AddStringToObject(response, "command", command);
    cJSON_AddBoolToObject(response, "ok", ok);
    return response;
}

void SendResponse(cJSON* response) {
    char* json = cJSON_PrintUnformatted(response);
    if (json != nullptr) {
        std::printf("%s%s\n", kResponsePrefix, json);
        std::fflush(stdout);
        cJSON_free(json);
    }
    cJSON_Delete(response);
}

void SendError(const char* command, const char* error) {
    auto response = NewResponse(command, false);
    cJSON_AddStringToObject(response, "error", error);
    SendResponse(response);
}

void AddGatewayStatus(cJSON* response) {
    Settings settings("websocket", false);
    cJSON_AddStringToObject(
        response, "url", settings.GetString("url").c_str());
    cJSON_AddStringToObject(
        response,
        "fallback_url",
        settings.GetString("fallback_url").c_str());
    cJSON_AddBoolToObject(
        response,
        "token_set",
        !settings.GetString("token").empty());
    cJSON_AddBoolToObject(response, "url_forced", IsUrlForced());
    cJSON_AddBoolToObject(
        response, "fallback_url_forced", IsFallbackUrlForced());
    cJSON_AddBoolToObject(response, "token_forced", IsTokenForced());
}

void SendStatus() {
    auto& app = Application::GetInstance();
    auto& wifi = WifiManager::GetInstance();
    auto response = NewResponse("status", true);
    cJSON_AddStringToObject(
        response,
        "firmware",
        esp_app_get_description()->version);
    cJSON_AddStringToObject(response, "product", "xc-body");
    cJSON_AddStringToObject(
        response,
        "project",
        esp_app_get_description()->project_name);
    cJSON_AddStringToObject(
        response,
        "state",
        DeviceStateMachine::GetStateName(app.GetDeviceState()));
    cJSON_AddBoolToObject(response, "wifi_connected", wifi.IsConnected());
    cJSON_AddStringToObject(response, "ssid", wifi.GetSsid().c_str());
    cJSON_AddStringToObject(response, "ip", wifi.GetIpAddress().c_str());
    cJSON_AddNumberToObject(response, "rssi", wifi.GetRssi());
    AddGatewayStatus(response);
    SendResponse(response);
}

bool GetOptionalString(
    const cJSON* request,
    const char* key,
    bool& provided,
    std::string& value) {
    const cJSON* item = cJSON_GetObjectItemCaseSensitive(request, key);
    provided = item != nullptr;
    if (!provided) {
        return true;
    }
    if (!IsString(item)) {
        return false;
    }
    value = item->valuestring;
    return true;
}

void SaveGatewayConfig(const cJSON* request) {
    bool url_provided = false;
    bool fallback_provided = false;
    bool token_provided = false;
    std::string url;
    std::string fallback_url;
    std::string token;
    if (!GetOptionalString(
            request, "url", url_provided, url) ||
        !GetOptionalString(
            request,
            "fallback_url",
            fallback_provided,
            fallback_url) ||
        !GetOptionalString(
            request, "token", token_provided, token)) {
        SendError("configure", "configuration values must be strings");
        return;
    }
    if (!url_provided && !fallback_provided && !token_provided) {
        SendError("configure", "no configuration value was provided");
        return;
    }
    if ((url_provided && !HasGatewayScheme(url)) ||
        (fallback_provided && !HasGatewayScheme(fallback_url))) {
        SendError("configure", "gateway URLs must use ws:// or wss://");
        return;
    }
    if ((url_provided && IsUrlForced()) ||
        (fallback_provided && IsFallbackUrlForced()) ||
        (token_provided && IsTokenForced())) {
        SendError(
            "configure",
            "requested value is overridden by this firmware build");
        return;
    }

    {
        Settings settings("websocket", true);
        auto save = [&settings](
                        const char* key,
                        bool provided,
                        const std::string& value) {
            if (!provided) {
                return;
            }
            if (value.empty()) {
                settings.EraseKey(key);
            } else {
                settings.SetString(key, value);
            }
        };
        save("url", url_provided, url);
        save("fallback_url", fallback_provided, fallback_url);
        save("token", token_provided, token);
    }

    auto response = NewResponse("configure", true);
    cJSON_AddStringToObject(response, "takes_effect", "after_reboot");
    AddGatewayStatus(response);
    SendResponse(response);
}

void ScheduleReboot() {
    SendResponse(NewResponse("reboot", true));
    Application::GetInstance().Schedule([]() {
        Application::GetInstance().Reboot();
    });
}

void ScheduleFirmwareUpdate(const cJSON* request) {
    const cJSON* url = cJSON_GetObjectItemCaseSensitive(request, "url");
    const cJSON* sha256 =
        cJSON_GetObjectItemCaseSensitive(request, "sha256");
    const cJSON* size = cJSON_GetObjectItemCaseSensitive(request, "size");
    if (!IsString(url) || !IsString(sha256) || !cJSON_IsNumber(size) ||
        size->valueint <= 0 || size->valueint > 0x3f0000) {
        SendError("update", "invalid firmware metadata");
        return;
    }

    std::string update_url = url->valuestring;
    std::string expected_sha256 = sha256->valuestring;
    int expected_size = size->valueint;
    auto response = NewResponse("update", true);
    cJSON_AddStringToObject(response, "status", "queued");
    cJSON_AddBoolToObject(response, "completed", false);
    SendResponse(response);
    Application::GetInstance().Schedule(
        [update_url, expected_sha256, expected_size]() {
            bool success = Application::GetInstance().UpgradeFirmware(
                update_url,
                "",
                expected_sha256,
                expected_size);
            if (!success) {
                ESP_LOGE(kTag, "XC Body firmware update failed");
            }
        });
}

void HandleRequest(const char* json) {
    cJSON* request = cJSON_Parse(json);
    if (request == nullptr || !cJSON_IsObject(request)) {
        cJSON_Delete(request);
        SendError("unknown", "invalid JSON request");
        return;
    }
    const cJSON* command =
        cJSON_GetObjectItemCaseSensitive(request, "command");
    if (!IsString(command)) {
        cJSON_Delete(request);
        SendError("unknown", "command must be a string");
        return;
    }

    if (std::strcmp(command->valuestring, "status") == 0) {
        SendStatus();
    } else if (std::strcmp(command->valuestring, "configure") == 0) {
        SaveGatewayConfig(request);
    } else if (std::strcmp(command->valuestring, "reboot") == 0) {
        ScheduleReboot();
    } else if (std::strcmp(command->valuestring, "update") == 0) {
        ScheduleFirmwareUpdate(request);
    } else {
        SendError(command->valuestring, "unsupported command");
    }
    cJSON_Delete(request);
}

void UsbControlTask(void*) {
    std::string line;
    line.reserve(kMaxRequestBytes);
    bool overflow = false;
    ESP_LOGI(kTag, "USB maintenance control ready");
    while (true) {
        int character = std::getchar();
        if (character == EOF) {
            clearerr(stdin);
            vTaskDelay(pdMS_TO_TICKS(20));
            continue;
        }
        if (character == '\r') {
            continue;
        }
        if (character != '\n') {
            if (line.size() < kMaxRequestBytes - 1) {
                line.push_back(static_cast<char>(character));
            } else {
                overflow = true;
            }
            continue;
        }
        if (overflow) {
            SendError("unknown", "request is too large");
        } else if (line.compare(
                       0,
                       sizeof(kRequestPrefix) - 1,
                       kRequestPrefix) == 0) {
            HandleRequest(line.c_str() + sizeof(kRequestPrefix) - 1);
        }
        line.clear();
        overflow = false;
    }
}

}  // namespace

void StartStackChanUsbControl() {
    BaseType_t created = xTaskCreate(
        UsbControlTask,
        "stackchan_usb",
        kTaskStackSize,
        nullptr,
        kTaskPriority,
        nullptr);
    if (created != pdPASS) {
        ESP_LOGE(kTag, "Failed to start USB maintenance control");
    }
}
