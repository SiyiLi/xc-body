#include "usb_control.h"

#include <algorithm>
#include <cstdio>
#include <cstdlib>
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
#include "ota.h"
#include "settings.h"

namespace {

constexpr char kRequestPrefix[] = "XC_BODY_REQUEST ";
constexpr char kResponsePrefix[] = "XC_BODY_RESPONSE ";
constexpr size_t kMaxRequestBytes = 1024;
constexpr uint32_t kTaskStackSize = 6144;
constexpr UBaseType_t kTaskPriority = tskIDLE_PRIORITY + 1;
const char* const kTag = "StackChanUsb";
StackChanExpressionPreviewer* expression_previewer = nullptr;
constexpr char kExpressionSettingsNamespace[] = "expressions";
constexpr int kIdleYaw = 0;
constexpr int kIdlePitch = 43;
constexpr int kMinCurveDurationMs = 100;
constexpr int kMaxStepDurationMs = 5000;
constexpr uint32_t kMaxRecipeDurationMs = 20000;

bool IsString(const cJSON* value) {
    return value != nullptr && cJSON_IsString(value) &&
        value->valuestring != nullptr;
}

bool IsInteger(const cJSON* value) {
    return cJSON_IsNumber(value) &&
        value->valuedouble == static_cast<double>(value->valueint);
}

bool IsExpressionName(const char* name) {
    if (name == nullptr) {
        return false;
    }
    for (const char* candidate : kStackChanExpressionNames) {
        if (std::strcmp(name, candidate) == 0) {
            return true;
        }
    }
    return false;
}

bool ParseExpressionPoint(
        const cJSON* value,
        StackChanExpressionPoint& point) {
    if (!cJSON_IsArray(value) || cJSON_GetArraySize(value) != 2) {
        return false;
    }
    const cJSON* yaw = cJSON_GetArrayItem(value, 0);
    const cJSON* pitch = cJSON_GetArrayItem(value, 1);
    if (!IsInteger(yaw) || !IsInteger(pitch)) {
        return false;
    }
    point = {yaw->valueint, pitch->valueint};
    return true;
}

bool ParseExpressionRecipe(
        const cJSON* value,
        StackChanExpressionRecipe& recipe) {
    if (!cJSON_IsObject(value)) {
        return false;
    }
    const cJSON* schema =
        cJSON_GetObjectItemCaseSensitive(value, "schema_version");
    const cJSON* steps = cJSON_GetObjectItemCaseSensitive(value, "steps");
    if (!IsInteger(schema) || schema->valueint != 1 ||
        !cJSON_IsArray(steps)) {
        return false;
    }
    int count = cJSON_GetArraySize(steps);
    if (count <= 0 ||
        count > static_cast<int>(kStackChanExpressionMaxSteps)) {
        return false;
    }
    recipe.schema_version = schema->valueint;
    recipe.step_count = static_cast<size_t>(count);
    for (int index = 0; index < count; ++index) {
        const cJSON* item = cJSON_GetArrayItem(steps, index);
        if (!cJSON_IsObject(item)) {
            return false;
        }
        const cJSON* type =
            cJSON_GetObjectItemCaseSensitive(item, "type");
        const cJSON* duration =
            cJSON_GetObjectItemCaseSensitive(item, "duration_ms");
        if (!IsString(type) || !IsInteger(duration)) {
            return false;
        }
        auto& step = recipe.steps[index];
        step.duration_ms = duration->valueint;
        if (std::strcmp(type->valuestring, "pause") == 0) {
            step.type = StackChanExpressionStepType::PAUSE;
            continue;
        }
        if (std::strcmp(type->valuestring, "curve") != 0) {
            return false;
        }
        step.type = StackChanExpressionStepType::CURVE;
        const cJSON* start =
            cJSON_GetObjectItemCaseSensitive(item, "start");
        const cJSON* via = cJSON_GetObjectItemCaseSensitive(item, "via");
        const cJSON* end = cJSON_GetObjectItemCaseSensitive(item, "end");
        if (!cJSON_IsArray(via) || cJSON_GetArraySize(via) != 2 ||
            !ParseExpressionPoint(start, step.points[0]) ||
            !ParseExpressionPoint(
                cJSON_GetArrayItem(via, 0), step.points[1]) ||
            !ParseExpressionPoint(
                cJSON_GetArrayItem(via, 1), step.points[2]) ||
            !ParseExpressionPoint(end, step.points[3])) {
            return false;
        }
    }
    return true;
}

bool ValidateExpressionRecipe(
        const StackChanExpressionRecipe& recipe,
        std::string& error) {
    StackChanExpressionPoint previous = {kIdleYaw, kIdlePitch};
    uint32_t total_ms = 0;
    bool previous_was_pause = false;
    for (size_t index = 0; index < recipe.step_count; ++index) {
        const auto& step = recipe.steps[index];
        if (step.type == StackChanExpressionStepType::PAUSE) {
            if (index == 0 || index + 1 == recipe.step_count ||
                previous_was_pause || step.duration_ms <= 0 ||
                step.duration_ms > kMaxStepDurationMs) {
                error = "pause must be bounded and between curves";
                return false;
            }
            total_ms += static_cast<uint32_t>(step.duration_ms);
            previous_was_pause = true;
            continue;
        }
        if (step.duration_ms < kMinCurveDurationMs ||
            step.duration_ms > kMaxStepDurationMs) {
            error = "curve duration must be between 100 and 5000 ms";
            return false;
        }
        for (const auto& point : step.points) {
            if (point.yaw < -90 || point.yaw > 90 ||
                point.pitch < 5 || point.pitch > 85) {
                error = "curve point exceeds servo limits";
                return false;
            }
        }
        if (step.points[0].yaw != previous.yaw ||
            step.points[0].pitch != previous.pitch) {
            error = "curve does not continue from the previous endpoint";
            return false;
        }
        previous = step.points[3];
        total_ms += static_cast<uint32_t>(step.duration_ms);
        previous_was_pause = false;
    }
    if (previous.yaw != kIdleYaw || previous.pitch != kIdlePitch) {
        error = "recipe must return to the idle pose";
        return false;
    }
    if (total_ms > kMaxRecipeDurationMs) {
        error = "recipe exceeds the 20 second movement budget";
        return false;
    }
    return true;
}

void AddExpressionPoint(cJSON* array, const StackChanExpressionPoint& point) {
    cJSON* encoded = cJSON_CreateArray();
    cJSON_AddItemToArray(encoded, cJSON_CreateNumber(point.yaw));
    cJSON_AddItemToArray(encoded, cJSON_CreateNumber(point.pitch));
    cJSON_AddItemToArray(array, encoded);
}

cJSON* EncodeExpressionRecipe(const StackChanExpressionRecipe& recipe) {
    cJSON* encoded = cJSON_CreateObject();
    cJSON_AddNumberToObject(
        encoded, "schema_version", recipe.schema_version);
    cJSON* steps = cJSON_AddArrayToObject(encoded, "steps");
    for (size_t index = 0; index < recipe.step_count; ++index) {
        const auto& step = recipe.steps[index];
        cJSON* item = cJSON_CreateObject();
        cJSON_AddStringToObject(
            item,
            "type",
            step.type == StackChanExpressionStepType::CURVE
                ? "curve" : "pause");
        cJSON_AddNumberToObject(item, "duration_ms", step.duration_ms);
        if (step.type == StackChanExpressionStepType::CURVE) {
            cJSON* start = cJSON_AddArrayToObject(item, "start");
            cJSON_AddItemToArray(
                start, cJSON_CreateNumber(step.points[0].yaw));
            cJSON_AddItemToArray(
                start, cJSON_CreateNumber(step.points[0].pitch));
            cJSON* via = cJSON_AddArrayToObject(item, "via");
            AddExpressionPoint(via, step.points[1]);
            AddExpressionPoint(via, step.points[2]);
            cJSON* end = cJSON_AddArrayToObject(item, "end");
            cJSON_AddItemToArray(
                end, cJSON_CreateNumber(step.points[3].yaw));
            cJSON_AddItemToArray(
                end, cJSON_CreateNumber(step.points[3].pitch));
        }
        cJSON_AddItemToArray(steps, item);
    }
    return encoded;
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

void AddOtaStatus(cJSON* response) {
    auto status = Ota::GetPolicyStatus();
    cJSON_AddBoolToObject(
        response,
        "automatic_ota_enabled",
        status.automatic_updates_enabled);
    cJSON_AddStringToObject(
        response,
        "ota_pending_version",
        status.pending_version.c_str());
    cJSON_AddStringToObject(
        response,
        "ota_failed_version",
        status.failed_version.c_str());
    cJSON_AddNumberToObject(
        response,
        "ota_rollback_reset_reason",
        status.rollback_reset_reason);
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
    AddOtaStatus(response);
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
    const cJSON* version =
        cJSON_GetObjectItemCaseSensitive(request, "version");
    const cJSON* sha256 =
        cJSON_GetObjectItemCaseSensitive(request, "sha256");
    const cJSON* size = cJSON_GetObjectItemCaseSensitive(request, "size");
    if (!IsString(url) || !IsString(version) || !IsString(sha256) ||
        !cJSON_IsNumber(size) ||
        size->valueint <= 0 || size->valueint > 0x3f0000) {
        SendError("update", "invalid firmware metadata");
        return;
    }

    std::string update_url = url->valuestring;
    std::string expected_version = version->valuestring;
    std::string expected_sha256 = sha256->valuestring;
    int expected_size = size->valueint;
    auto response = NewResponse("update", true);
    cJSON_AddStringToObject(response, "status", "queued");
    cJSON_AddBoolToObject(response, "completed", false);
    SendResponse(response);
    Application::GetInstance().Schedule(
        [update_url, expected_version, expected_sha256, expected_size]() {
            bool success = Application::GetInstance().UpgradeFirmware(
                update_url,
                expected_version,
                expected_sha256,
                expected_size);
            if (!success) {
                ESP_LOGE(kTag, "XC Body firmware update failed");
            }
        });
}

void SetAutomaticOta(const cJSON* request) {
    const cJSON* enabled = cJSON_GetObjectItemCaseSensitive(
        request, "enabled");
    if (!cJSON_IsBool(enabled)) {
        SendError("automatic_ota", "enabled must be a boolean");
        return;
    }

    bool value = cJSON_IsTrue(enabled);
    Ota::SetAutomaticUpdatesEnabled(value);
    auto response = NewResponse("automatic_ota", true);
    AddOtaStatus(response);
    SendResponse(response);
}

void HandleExpressionRequest(const cJSON* request, const char* command) {
    const cJSON* name = cJSON_GetObjectItemCaseSensitive(request, "name");
    if (!IsString(name) || !IsExpressionName(name->valuestring)) {
        SendError(command, "unknown expression");
        return;
    }

    std::string error;
    if (std::strcmp(command, "expression_show") == 0) {
        Settings settings(kExpressionSettingsNamespace, false);
        std::string encoded = settings.GetString(name->valuestring);
        if (encoded.empty()) {
            SendError(command, "expression is not calibrated");
            return;
        }
        cJSON* stored = cJSON_Parse(encoded.c_str());
        StackChanExpressionRecipe recipe;
        bool valid = ParseExpressionRecipe(stored, recipe) &&
            ValidateExpressionRecipe(recipe, error);
        cJSON_Delete(stored);
        if (!valid) {
            SendError(command, "stored expression recipe is invalid");
            return;
        }
        auto response = NewResponse(command, true);
        cJSON_AddStringToObject(response, "name", name->valuestring);
        cJSON_AddItemToObject(
            response, "recipe", EncodeExpressionRecipe(recipe));
        SendResponse(response);
        return;
    }

    const cJSON* value =
        cJSON_GetObjectItemCaseSensitive(request, "recipe");
    StackChanExpressionRecipe recipe;
    if (!ParseExpressionRecipe(value, recipe)) {
        SendError(command, "recipe must use schema 1 curve/pause steps");
        return;
    }
    if (!ValidateExpressionRecipe(recipe, error)) {
        SendError(command, error.c_str());
        return;
    }
    if (std::strcmp(command, "expression_preview") == 0) {
        if (expression_previewer == nullptr ||
            !expression_previewer->PreviewExpression(
                name->valuestring, recipe, error)) {
            SendError(
                command,
                error.empty() ? "expression preview is unavailable"
                              : error.c_str());
            return;
        }
    } else {
        cJSON* stored = EncodeExpressionRecipe(recipe);
        char* encoded = cJSON_PrintUnformatted(stored);
        cJSON_Delete(stored);
        if (encoded == nullptr) {
            SendError(command, "could not encode expression recipe");
            return;
        }
        Settings settings(kExpressionSettingsNamespace, true);
        settings.SetString(name->valuestring, encoded);
        cJSON_free(encoded);
    }
    auto response = NewResponse(command, true);
    cJSON_AddStringToObject(response, "name", name->valuestring);
    cJSON_AddItemToObject(
        response, "recipe", EncodeExpressionRecipe(recipe));
    if (std::strcmp(command, "expression_preview") == 0) {
        cJSON_AddBoolToObject(response, "started", true);
    } else {
        cJSON_AddStringToObject(response, "persistence", "nvs");
    }
    SendResponse(response);
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
    } else if (std::strcmp(
                   command->valuestring, "automatic_ota") == 0) {
        SetAutomaticOta(request);
    } else if (
        std::strcmp(command->valuestring, "expression_preview") == 0 ||
        std::strcmp(command->valuestring, "expression_save") == 0 ||
        std::strcmp(command->valuestring, "expression_show") == 0) {
        HandleExpressionRequest(request, command->valuestring);
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

void StartStackChanUsbControl(StackChanExpressionPreviewer* expressions) {
    expression_previewer = expressions;
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
