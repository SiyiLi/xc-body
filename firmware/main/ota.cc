#include "ota.h"
#include "system_info.h"
#include "settings.h"
#include "assets/lang_config.h"

#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <cJSON.h>
#include <esp_log.h>
#include <esp_partition.h>
#include <esp_ota_ops.h>
#include <esp_app_format.h>
#include <esp_attr.h>
#include <esp_efuse.h>
#include <esp_efuse_table.h>
#include <esp_heap_caps.h>
#include <esp_system.h>
#include <mbedtls/sha256.h>
#ifdef SOC_HMAC_SUPPORTED
#include <esp_hmac.h>
#endif

#include <array>
#include <cctype>
#include <cstdint>
#include <cstring>
#include <algorithm>
#include <limits>

#define TAG "Ota"

namespace {

constexpr char kManifestProduct[] = "xc-body";
constexpr char kStackChanProjectName[] = "xc_body_stackchan";
constexpr char kOtaSettingsNamespace[] = "xc_ota";
constexpr char kAutomaticUpdatesKey[] = "auto_enabled";
constexpr char kPendingVersionKey[] = "pending_ver";
constexpr char kPendingSlotKey[] = "pending_slot";
constexpr char kFailedVersionKey[] = "failed_ver";
constexpr char kRollbackResetReasonKey[] = "reset_reason";

constexpr uint32_t kOtaDiagnosticMagic = 0x58434f54;

enum class OtaDiagnosticState : uint32_t {
    kNone,
    kInProgress,
    kInterrupted,
    kFailed,
    kComplete,
};

enum class OtaDiagnosticStage : uint32_t {
    kNone,
    kConnect,
    kRead,
    kBegin,
    kWrite,
    kVerify,
    kActivate,
    kComplete,
};

struct OtaRtcDiagnostics {
    uint32_t magic;
    OtaDiagnosticState state;
    OtaDiagnosticStage stage;
    uint32_t bytes_received;
    uint32_t expected_size;
    bool has_previous_interruption;
    OtaDiagnosticStage previous_stage;
    uint32_t previous_bytes_received;
    uint32_t previous_expected_size;
    int previous_reset_reason;
};

RTC_NOINIT_ATTR OtaRtcDiagnostics ota_diagnostics;

bool HasValidOtaDiagnostics() {
    return ota_diagnostics.magic == kOtaDiagnosticMagic &&
        ota_diagnostics.state <= OtaDiagnosticState::kComplete &&
        ota_diagnostics.stage <= OtaDiagnosticStage::kComplete;
}

const char* OtaStateName(OtaDiagnosticState state) {
    switch (state) {
        case OtaDiagnosticState::kInProgress:
            return "in_progress";
        case OtaDiagnosticState::kInterrupted:
            return "interrupted";
        case OtaDiagnosticState::kFailed:
            return "failed";
        case OtaDiagnosticState::kComplete:
            return "complete";
        case OtaDiagnosticState::kNone:
        default:
            return "none";
    }
}

const char* OtaStageName(OtaDiagnosticStage stage) {
    switch (stage) {
        case OtaDiagnosticStage::kConnect:
            return "connect";
        case OtaDiagnosticStage::kRead:
            return "read";
        case OtaDiagnosticStage::kBegin:
            return "begin";
        case OtaDiagnosticStage::kWrite:
            return "write";
        case OtaDiagnosticStage::kVerify:
            return "verify";
        case OtaDiagnosticStage::kActivate:
            return "activate";
        case OtaDiagnosticStage::kComplete:
            return "complete";
        case OtaDiagnosticStage::kNone:
        default:
            return "none";
    }
}

void BeginOtaDiagnostics(size_t expected_size) {
    bool preserve_interruption = HasValidOtaDiagnostics() &&
        ota_diagnostics.state == OtaDiagnosticState::kInterrupted;
    if (!preserve_interruption) {
        ota_diagnostics.has_previous_interruption = false;
    }
    ota_diagnostics.magic = kOtaDiagnosticMagic;
    ota_diagnostics.state = OtaDiagnosticState::kInProgress;
    ota_diagnostics.stage = OtaDiagnosticStage::kConnect;
    ota_diagnostics.bytes_received = 0;
    ota_diagnostics.expected_size = expected_size;
}

void RecordOtaStage(OtaDiagnosticStage stage, size_t bytes_received) {
    ota_diagnostics.stage = stage;
    ota_diagnostics.bytes_received = bytes_received;
}

class OtaDiagnosticAttempt {
public:
    explicit OtaDiagnosticAttempt(size_t expected_size) {
        BeginOtaDiagnostics(expected_size);
    }

    ~OtaDiagnosticAttempt() {
        if (!complete_) {
            ota_diagnostics.state = OtaDiagnosticState::kFailed;
        }
    }

    void Complete() {
        RecordOtaStage(
            OtaDiagnosticStage::kComplete,
            ota_diagnostics.expected_size);
        ota_diagnostics.state = OtaDiagnosticState::kComplete;
        complete_ = true;
    }

private:
    bool complete_ = false;
};

void ClearPendingUpdate(Settings& settings) {
    settings.EraseKey(kPendingVersionKey);
    settings.EraseKey(kPendingSlotKey);
}

bool IsHttpsUrl(const std::string& url) {
    constexpr size_t kSchemeLength = 8;
    if (url.rfind("https://", 0) != 0) {
        return false;
    }
    size_t authority_end = url.find_first_of("/?#", kSchemeLength);
    if (authority_end == std::string::npos) {
        authority_end = url.size();
    }
    if (authority_end == kSchemeLength ||
        url.find('@', kSchemeLength) < authority_end) {
        return false;
    }
    size_t host_end = url.find(':', kSchemeLength);
    return host_end == std::string::npos || host_end > kSchemeLength;
}

bool IsSha256Hex(const std::string& value) {
    return value.size() == 64 && std::all_of(
        value.begin(),
        value.end(),
        [](unsigned char character) {
            return std::isdigit(character) ||
                (character >= 'a' && character <= 'f');
        });
}

bool ParseSemanticVersion(
    const std::string& version,
    std::array<uint32_t, 3>& parts) {
    size_t start = 0;
    for (size_t index = 0; index < parts.size(); ++index) {
        size_t end = version.find('.', start);
        if ((index < parts.size() - 1 && end == std::string::npos) ||
            (index == parts.size() - 1 && end != std::string::npos)) {
            return false;
        }

        size_t length = (end == std::string::npos ? version.size() : end) -
            start;
        if (length == 0 ||
            (length > 1 && version[start] == '0')) {
            return false;
        }

        uint32_t value = 0;
        for (size_t offset = 0; offset < length; ++offset) {
            unsigned char character = version[start + offset];
            if (!std::isdigit(character)) {
                return false;
            }
            uint32_t digit = character - '0';
            if (value >
                (std::numeric_limits<uint32_t>::max() - digit) / 10) {
                return false;
            }
            value = value * 10 + digit;
        }
        parts[index] = value;
        if (end != std::string::npos) {
            start = end + 1;
        }
    }
    return true;
}

bool IsNewerSemanticVersion(
    const std::array<uint32_t, 3>& current,
    const std::array<uint32_t, 3>& candidate) {
    return candidate > current;
}

std::string Sha256Hex(const std::array<unsigned char, 32>& digest) {
    static constexpr char kHex[] = "0123456789abcdef";
    std::string result(64, '0');
    for (size_t index = 0; index < digest.size(); ++index) {
        result[index * 2] = kHex[digest[index] >> 4];
        result[index * 2 + 1] = kHex[digest[index] & 0x0f];
    }
    return result;
}

class Sha256Context {
public:
    Sha256Context() {
        mbedtls_sha256_init(&context_);
    }

    ~Sha256Context() {
        mbedtls_sha256_free(&context_);
    }

    bool Start() {
        return mbedtls_sha256_starts(&context_, 0) == 0;
    }

    bool Update(const char* data, size_t size) {
        return mbedtls_sha256_update(
            &context_,
            reinterpret_cast<const unsigned char*>(data),
            size) == 0;
    }

    bool Finish(std::array<unsigned char, 32>& digest) {
        return mbedtls_sha256_finish(&context_, digest.data()) == 0;
    }

private:
    mbedtls_sha256_context context_;
};

}  // namespace


Ota::Ota() {
    if (HasValidOtaDiagnostics() &&
        ota_diagnostics.state == OtaDiagnosticState::kInProgress) {
        ota_diagnostics.has_previous_interruption = true;
        ota_diagnostics.previous_stage = ota_diagnostics.stage;
        ota_diagnostics.previous_bytes_received =
            ota_diagnostics.bytes_received;
        ota_diagnostics.previous_expected_size =
            ota_diagnostics.expected_size;
        ota_diagnostics.previous_reset_reason = esp_reset_reason();
        ota_diagnostics.state = OtaDiagnosticState::kInterrupted;
        ESP_LOGE(
            TAG,
            "Previous OTA interrupted at %s (%lu/%lu bytes), reset=%d",
            OtaStageName(ota_diagnostics.previous_stage),
            static_cast<unsigned long>(
                ota_diagnostics.previous_bytes_received),
            static_cast<unsigned long>(
                ota_diagnostics.previous_expected_size),
            ota_diagnostics.previous_reset_reason);
    }
#ifdef ESP_EFUSE_BLOCK_USR_DATA
    // Read Serial Number from efuse user_data
    uint8_t serial_number[33] = {0};
    if (esp_efuse_read_field_blob(ESP_EFUSE_USER_DATA, serial_number, 32 * 8) == ESP_OK) {
        if (serial_number[0] == 0) {
            has_serial_number_ = false;
        } else {
            serial_number_ = std::string(reinterpret_cast<char*>(serial_number), 32);
            has_serial_number_ = true;
        }
    }
#endif
}

Ota::~Ota() {
}

OtaPolicyStatus Ota::GetPolicyStatus() {
    Settings settings(kOtaSettingsNamespace, false);
    OtaPolicyStatus status;
    status.automatic_updates_enabled = settings.GetBool(
        kAutomaticUpdatesKey, true);
    status.pending_version = settings.GetString(kPendingVersionKey);
    status.failed_version = settings.GetString(kFailedVersionKey);
    status.rollback_reset_reason = settings.GetInt(
        kRollbackResetReasonKey, 0);
    return status;
}

OtaDownloadDiagnostics Ota::GetDownloadDiagnostics() {
    OtaDownloadDiagnostics result;
    if (!HasValidOtaDiagnostics()) {
        return result;
    }

    result.state = OtaStateName(ota_diagnostics.state);
    result.stage = OtaStageName(ota_diagnostics.stage);
    result.bytes_received = ota_diagnostics.bytes_received;
    result.expected_size = ota_diagnostics.expected_size;
    if (result.expected_size > 0) {
        result.progress = static_cast<int>(
            static_cast<uint64_t>(result.bytes_received) * 100 /
            result.expected_size);
    }
    result.has_previous_interruption =
        ota_diagnostics.has_previous_interruption;
    if (result.has_previous_interruption) {
        result.previous_stage = OtaStageName(
            ota_diagnostics.previous_stage);
        result.previous_bytes_received =
            ota_diagnostics.previous_bytes_received;
        result.previous_expected_size =
            ota_diagnostics.previous_expected_size;
        result.previous_reset_reason =
            ota_diagnostics.previous_reset_reason;
    }
    return result;
}

void Ota::SetAutomaticUpdatesEnabled(bool enabled) {
    Settings settings(kOtaSettingsNamespace, true);
    settings.SetBool(kAutomaticUpdatesKey, enabled);
    if (enabled) {
        settings.EraseKey(kFailedVersionKey);
        settings.EraseKey(kRollbackResetReasonKey);
    }
    ESP_LOGI(
        TAG,
        "Automatic OTA %s by local control",
        enabled ? "enabled" : "disabled");
}

void Ota::RecordRollbackIfNeeded() {
    auto running = esp_ota_get_running_partition();
    if (running == nullptr) {
        return;
    }

    Settings read_settings(kOtaSettingsNamespace, false);
    std::string pending_version = read_settings.GetString(
        kPendingVersionKey);
    std::string pending_slot = read_settings.GetString(kPendingSlotKey);
    if (pending_version.empty() || pending_slot.empty() ||
        pending_slot == running->label) {
        return;
    }

    auto failed = esp_ota_get_last_invalid_partition();
    if (failed == nullptr || pending_slot != failed->label) {
        ESP_LOGW(
            TAG,
            "Pending OTA %s expected slot %s, but no matching failed "
            "partition was found",
            pending_version.c_str(),
            pending_slot.c_str());
        return;
    }

    esp_ota_img_states_t state;
    if (esp_ota_get_state_partition(failed, &state) != ESP_OK ||
        (state != ESP_OTA_IMG_ABORTED && state != ESP_OTA_IMG_INVALID)) {
        return;
    }

    int reset_reason = static_cast<int>(esp_reset_reason());
    {
        Settings settings(kOtaSettingsNamespace, true);
        settings.SetBool(kAutomaticUpdatesKey, false);
        settings.SetString(kFailedVersionKey, pending_version);
        settings.SetInt(kRollbackResetReasonKey, reset_reason);
        ClearPendingUpdate(settings);
    }
    ESP_LOGE(
        TAG,
        "Firmware %s rolled back from %s (reset reason %d); automatic "
        "OTA is disabled",
        pending_version.c_str(),
        pending_slot.c_str(),
        reset_reason);
}

std::string Ota::GetCheckVersionUrl() {
    Settings settings("wifi", false);
    std::string url = settings.GetString("ota_url");
    if (url.empty()) {
        url = CONFIG_OTA_URL;
    }
    return url;
}

std::unique_ptr<Http> Ota::SetupHttp() {
    auto& board = Board::GetInstance();
    auto network = board.GetNetwork();
    auto http = network->CreateHttp(0);
    auto user_agent = SystemInfo::GetUserAgent();
    http->SetHeader("Activation-Version", has_serial_number_ ? "2" : "1");
    http->SetHeader("Device-Id", SystemInfo::GetMacAddress().c_str());
    http->SetHeader("Client-Id", board.GetUuid());
    if (has_serial_number_) {
        http->SetHeader("Serial-Number", serial_number_.c_str());
        ESP_LOGI(TAG, "Setup HTTP, User-Agent: %s, Serial-Number: %s", user_agent.c_str(), serial_number_.c_str());
    }
    http->SetHeader("User-Agent", user_agent);
    http->SetHeader("Accept-Language", Lang::CODE);
    http->SetHeader("Content-Type", "application/json");

    return http;
}

// Parse the XC Body firmware manifest.
esp_err_t Ota::CheckVersion() {
    auto app_desc = esp_app_get_description();

    // Check if there is a new firmware version available
    current_version_ = app_desc->version;
    ESP_LOGI(TAG, "Current version: %s", current_version_.c_str());

    std::string url = GetCheckVersionUrl();
    if (url.length() < 10) {
        ESP_LOGE(TAG, "Check version URL is not properly set");
        return ESP_ERR_INVALID_ARG;
    }

    auto http = SetupHttp();

    if (!IsHttpsUrl(url)) {
        ESP_LOGE(TAG, "XC Body OTA manifest URL must use HTTPS");
        return ESP_ERR_INVALID_ARG;
    }

    if (!http->Open("GET", url)) {
        int last_error = http->GetLastError();
        ESP_LOGE(TAG, "Failed to open HTTP connection, code=0x%x", last_error);
        return last_error;
    }

    auto status_code = http->GetStatusCode();
    if (status_code != 200) {
        ESP_LOGE(TAG, "Failed to check version, status code: %d", status_code);
        return status_code;
    }

    std::string data = http->ReadAll();
    http->Close();

    // Response: { "firmware": { "version": "1.0.0", "url": "http://" } }
    // Parse the JSON response and check if the version is newer
    // If it is, set has_new_version_ to true and store the new version and URL
    
    cJSON *root = cJSON_Parse(data.c_str());
    if (root == NULL) {
        ESP_LOGE(TAG, "Failed to parse JSON response");
        return ESP_ERR_INVALID_RESPONSE;
    }

    cJSON *schema_version = cJSON_GetObjectItem(root, "schema_version");
    cJSON *product = cJSON_GetObjectItem(root, "product");
    cJSON *hardware = cJSON_GetObjectItem(root, "hardware");
    if (!cJSON_IsNumber(schema_version) || schema_version->valueint != 1 ||
        !cJSON_IsString(product) ||
        strcmp(product->valuestring, kManifestProduct) != 0 ||
        !cJSON_IsString(hardware) ||
        strcmp(hardware->valuestring, BOARD_NAME) != 0) {
        ESP_LOGE(TAG, "Rejected incompatible XC Body OTA manifest");
        cJSON_Delete(root);
        return ESP_ERR_INVALID_RESPONSE;
    }

    has_activation_code_ = false;
    has_activation_challenge_ = false;
    cJSON *activation = cJSON_GetObjectItem(root, "activation");
    if (cJSON_IsObject(activation)) {
        cJSON* message = cJSON_GetObjectItem(activation, "message");
        if (cJSON_IsString(message)) {
            activation_message_ = message->valuestring;
        }
        cJSON* code = cJSON_GetObjectItem(activation, "code");
        if (cJSON_IsString(code)) {
            activation_code_ = code->valuestring;
            has_activation_code_ = true;
        }
        cJSON* challenge = cJSON_GetObjectItem(activation, "challenge");
        if (cJSON_IsString(challenge)) {
            activation_challenge_ = challenge->valuestring;
            has_activation_challenge_ = true;
        }
        cJSON* timeout_ms = cJSON_GetObjectItem(activation, "timeout_ms");
        if (cJSON_IsNumber(timeout_ms)) {
            activation_timeout_ms_ = timeout_ms->valueint;
        }
    }

    has_mqtt_config_ = false;
    cJSON *mqtt = cJSON_GetObjectItem(root, "mqtt");
    if (cJSON_IsObject(mqtt)) {
        Settings settings("mqtt", true);
        cJSON *item = NULL;
        cJSON_ArrayForEach(item, mqtt) {
            if (cJSON_IsString(item)) {
                if (settings.GetString(item->string) != item->valuestring) {
                    settings.SetString(item->string, item->valuestring);
                }
            } else if (cJSON_IsNumber(item)) {
                if (settings.GetInt(item->string) != item->valueint) {
                    settings.SetInt(item->string, item->valueint);
                }
            }
        }
        has_mqtt_config_ = true;
    } else {
        ESP_LOGI(TAG, "No mqtt section found !");
    }

    has_websocket_config_ = false;
#if CONFIG_DISABLE_OTA_WEBSOCKET_CONFIG
    // The stackchan-mcp build owns the WebSocket URL/token/fallback_url
    // via the WiFi config UI and the CONFIG_DEFAULT_WEBSOCKET_* Kconfig
    // values. The upstream xiaozhi OTA server returns its own websocket
    // section pointing at api.tenclass.net, which used to overwrite the
    // user's NVS values on every boot (GitHub issue #110). We skip the
    // write-back entirely; the section, if present, is logged for
    // diagnostics only.
    if (cJSON_HasObjectItem(root, "websocket")) {
        ESP_LOGI(TAG, "OTA response includes a websocket section; ignored "
                      "(CONFIG_DISABLE_OTA_WEBSOCKET_CONFIG=y).");
    }
#else
    cJSON *websocket = cJSON_GetObjectItem(root, "websocket");
    if (cJSON_IsObject(websocket)) {
        Settings settings("websocket", true);
        cJSON *item = NULL;
        cJSON_ArrayForEach(item, websocket) {
            if (cJSON_IsString(item)) {
                if (settings.GetString(item->string) != item->valuestring) {
                    settings.SetString(item->string, item->valuestring);
                }
            } else if (cJSON_IsNumber(item)) {
                if (settings.GetInt(item->string) != item->valueint) {
                    settings.SetInt(item->string, item->valueint);
                }
            }
        }
        has_websocket_config_ = true;
    } else {
        ESP_LOGI(TAG, "No websocket section found!");
    }
#endif

    has_server_time_ = false;
    cJSON *server_time = cJSON_GetObjectItem(root, "server_time");
    if (cJSON_IsObject(server_time)) {
        cJSON *timestamp = cJSON_GetObjectItem(server_time, "timestamp");
        cJSON *timezone_offset = cJSON_GetObjectItem(server_time, "timezone_offset");
        
        if (cJSON_IsNumber(timestamp)) {
            // 设置系统时间
            struct timeval tv;
            double ts = timestamp->valuedouble;
            
            // 如果有时区偏移，计算本地时间
            if (cJSON_IsNumber(timezone_offset)) {
                ts += (timezone_offset->valueint * 60 * 1000); // 转换分钟为毫秒
            }
            
            tv.tv_sec = (time_t)(ts / 1000);  // 转换毫秒为秒
            tv.tv_usec = (suseconds_t)((long long)ts % 1000) * 1000;  // 剩余的毫秒转换为微秒
            settimeofday(&tv, NULL);
            has_server_time_ = true;
        }
    } else {
        ESP_LOGW(TAG, "No server_time section found!");
    }

    has_new_version_ = false;
    firmware_sha256_.clear();
    firmware_size_ = 0;
    cJSON *firmware = cJSON_GetObjectItem(root, "firmware");
    if (cJSON_IsObject(firmware)) {
        cJSON *version = cJSON_GetObjectItem(firmware, "version");
        if (cJSON_IsString(version)) {
            firmware_version_ = version->valuestring;
        }
        cJSON *url = cJSON_GetObjectItem(firmware, "url");
        if (cJSON_IsString(url)) {
            firmware_url_ = url->valuestring;
        }
        cJSON *sha256 = cJSON_GetObjectItem(firmware, "sha256");
        if (cJSON_IsString(sha256)) {
            firmware_sha256_ = sha256->valuestring;
        }
        cJSON *size = cJSON_GetObjectItem(firmware, "size");
        if (cJSON_IsNumber(size) && size->valueint > 0) {
            firmware_size_ = static_cast<size_t>(size->valueint);
        }

        std::array<uint32_t, 3> current_parts;
        std::array<uint32_t, 3> firmware_parts;
        if (cJSON_IsString(version) && cJSON_IsString(url) &&
            cJSON_IsString(sha256) && cJSON_IsNumber(size) &&
            IsHttpsUrl(firmware_url_) &&
            IsSha256Hex(firmware_sha256_) && firmware_size_ > 0 &&
            ParseSemanticVersion(current_version_, current_parts) &&
            ParseSemanticVersion(firmware_version_, firmware_parts)) {
            has_new_version_ = IsNewerSemanticVersion(
                current_parts, firmware_parts);
            if (has_new_version_) {
                ESP_LOGI(TAG, "New version available: %s", firmware_version_.c_str());
            } else {
                ESP_LOGI(TAG, "Current is the latest version");
            }
        } else {
            ESP_LOGE(TAG, "XC Body OTA firmware entry is incomplete or invalid");
            cJSON_Delete(root);
            return ESP_ERR_INVALID_RESPONSE;
        }
    } else {
        ESP_LOGE(TAG, "XC Body OTA manifest has no firmware section");
        cJSON_Delete(root);
        return ESP_ERR_INVALID_RESPONSE;
    }

    has_assets_ = false;
    assets_version_.clear();
    assets_url_.clear();
    assets_sha256_.clear();
    assets_size_ = 0;
    cJSON *assets = cJSON_GetObjectItem(root, "assets");
    if (cJSON_IsObject(assets)) {
        cJSON *version = cJSON_GetObjectItem(assets, "version");
        cJSON *url = cJSON_GetObjectItem(assets, "url");
        cJSON *sha256 = cJSON_GetObjectItem(assets, "sha256");
        cJSON *size = cJSON_GetObjectItem(assets, "size");
        if (cJSON_IsString(version)) {
            assets_version_ = version->valuestring;
        }
        if (cJSON_IsString(url)) {
            assets_url_ = url->valuestring;
        }
        if (cJSON_IsString(sha256)) {
            assets_sha256_ = sha256->valuestring;
        }
        if (cJSON_IsNumber(size) && size->valueint > 0 &&
            size->valuedouble == static_cast<double>(size->valueint)) {
            assets_size_ = static_cast<size_t>(size->valueint);
        }

        std::array<uint32_t, 3> assets_parts;
        auto assets_partition = esp_partition_find_first(
            ESP_PARTITION_TYPE_ANY,
            ESP_PARTITION_SUBTYPE_ANY,
            "assets");
        if (!cJSON_IsString(version) || !cJSON_IsString(url) ||
            !cJSON_IsString(sha256) || !cJSON_IsNumber(size) ||
            assets_version_ != firmware_version_ ||
            !ParseSemanticVersion(assets_version_, assets_parts) ||
            !IsHttpsUrl(assets_url_) || !IsSha256Hex(assets_sha256_) ||
            assets_partition == nullptr || assets_size_ == 0 ||
            assets_size_ > assets_partition->size) {
            ESP_LOGE(TAG, "XC Body OTA assets entry is invalid");
            cJSON_Delete(root);
            return ESP_ERR_INVALID_RESPONSE;
        }
        has_assets_ = true;
        ESP_LOGI(
            TAG,
            "Manifest assets: version=%s sha256=%s size=%u",
            assets_version_.c_str(),
            assets_sha256_.c_str(),
            assets_size_);
    } else {
        ESP_LOGE(TAG, "XC Body OTA manifest has no assets section");
        cJSON_Delete(root);
        return ESP_ERR_INVALID_RESPONSE;
    }

    cJSON_Delete(root);
    return ESP_OK;
}

void Ota::MarkCurrentVersionValid() {
    auto partition = esp_ota_get_running_partition();
    if (strcmp(partition->label, "factory") == 0) {
        ESP_LOGI(TAG, "Running from factory partition, skipping");
        return;
    }

    ESP_LOGI(TAG, "Running partition: %s", partition->label);
    esp_ota_img_states_t state;
    if (esp_ota_get_state_partition(partition, &state) != ESP_OK) {
        ESP_LOGE(TAG, "Failed to get state of partition");
        return;
    }

    if (state == ESP_OTA_IMG_PENDING_VERIFY) {
        ESP_LOGI(TAG, "Marking firmware as valid");
        esp_err_t err = esp_ota_mark_app_valid_cancel_rollback();
        if (err != ESP_OK) {
            ESP_LOGE(
                TAG,
                "Failed to mark firmware as valid: %s",
                esp_err_to_name(err));
            return;
        }
    }

    Settings read_settings(kOtaSettingsNamespace, false);
    if (read_settings.GetString(kPendingSlotKey) == partition->label) {
        Settings settings(kOtaSettingsNamespace, true);
        ClearPendingUpdate(settings);
        ESP_LOGI(TAG, "Cleared pending OTA marker after health confirmation");
    }
}

bool Ota::Upgrade(
    const std::string& firmware_url,
    const std::string& expected_version,
    const std::string& expected_sha256,
    size_t expected_size,
    std::function<void(int progress, size_t speed)> callback) {
    std::array<uint32_t, 3> version_parts;
    if (!IsHttpsUrl(firmware_url) || !IsSha256Hex(expected_sha256) ||
        !ParseSemanticVersion(expected_version, version_parts) ||
        expected_version.size() >= sizeof(esp_app_desc_t::version) ||
        expected_size == 0) {
        ESP_LOGE(TAG, "Rejected invalid XC Body firmware metadata");
        return false;
    }

    OtaDiagnosticAttempt diagnostic_attempt(expected_size);

    ESP_LOGI(TAG, "Starting verified XC Body firmware download");
    esp_ota_handle_t update_handle = 0;
    auto update_partition = esp_ota_get_next_update_partition(NULL);
    if (update_partition == NULL) {
        ESP_LOGE(TAG, "Failed to get update partition");
        return false;
    }
    if (expected_size > update_partition->size) {
        ESP_LOGE(TAG, "Firmware image exceeds OTA partition");
        return false;
    }

    ESP_LOGI(TAG, "Writing to partition %s at offset 0x%lx", update_partition->label, update_partition->address);
    bool image_header_checked = false;
    bool update_started = false;
    std::string image_header;

    auto network = Board::GetInstance().GetNetwork();
    auto http = network->CreateHttp(0);
    RecordOtaStage(OtaDiagnosticStage::kConnect, 0);
    if (!http->Open("GET", firmware_url)) {
        ESP_LOGE(TAG, "Failed to open HTTP connection");
        return false;
    }

    if (http->GetStatusCode() != 200) {
        ESP_LOGE(TAG, "Failed to get firmware, status code: %d", http->GetStatusCode());
        http->Close();
        return false;
    }

    size_t content_length = http->GetBodyLength();
    if (content_length != expected_size) {
        ESP_LOGE(
            TAG,
            "Firmware size mismatch: HTTP=%u manifest=%u",
            content_length,
            expected_size);
        http->Close();
        return false;
    }

    constexpr size_t PAGE_SIZE = 4096;
    char* buffer = (char*)heap_caps_malloc(PAGE_SIZE, MALLOC_CAP_INTERNAL);
    if (buffer == nullptr) {
        ESP_LOGE(TAG, "Failed to allocate buffer");
        http->Close();
        return false;
    }

    Sha256Context sha256;
    if (!sha256.Start()) {
        ESP_LOGE(TAG, "Failed to initialize SHA-256");
        http->Close();
        heap_caps_free(buffer);
        return false;
    }

    auto fail_download = [&]() {
        if (update_started) {
            esp_ota_abort(update_handle);
        }
        http->Close();
        heap_caps_free(buffer);
        return false;
    };

    size_t buffer_offset = 0;  // Current data size in buffer
    size_t total_read = 0, recent_read = 0;
    auto last_calc_time = esp_timer_get_time();
    while (true) {
        size_t read_offset = buffer_offset;
        RecordOtaStage(OtaDiagnosticStage::kRead, total_read);
        int ret = http->Read(buffer + buffer_offset, PAGE_SIZE - buffer_offset);
        if (ret < 0) {
            ESP_LOGE(TAG, "Failed to read HTTP data: %s", esp_err_to_name(ret));
            return fail_download();
        }
        if (ret > 0 && !sha256.Update(buffer + read_offset, ret)) {
            ESP_LOGE(TAG, "Failed to update firmware SHA-256");
            return fail_download();
        }

        // Calculate speed and progress every second
        recent_read += ret;
        total_read += ret;
        buffer_offset += ret;
        if (total_read > expected_size) {
            ESP_LOGE(TAG, "Firmware download exceeded manifest size");
            return fail_download();
        }
        if (esp_timer_get_time() - last_calc_time >= 1000000 || ret == 0) {
            size_t progress = total_read * 100 / content_length;
            ESP_LOGI(TAG, "Progress: %u%% (%u/%u), Speed: %uB/s", progress, total_read, content_length, recent_read);
            if (callback) {
                callback(progress, recent_read);
            }
            last_calc_time = esp_timer_get_time();
            recent_read = 0;
        }

        if (!image_header_checked) {
            image_header.append(buffer + read_offset, ret);
            if (image_header.size() >= sizeof(esp_image_header_t) + sizeof(esp_image_segment_header_t) + sizeof(esp_app_desc_t)) {
                esp_app_desc_t new_app_info;
                memcpy(&new_app_info, image_header.data() + sizeof(esp_image_header_t) + sizeof(esp_image_segment_header_t), sizeof(esp_app_desc_t));
                if (new_app_info.magic_word != ESP_APP_DESC_MAGIC_WORD ||
                    strncmp(
                        new_app_info.project_name,
                        kStackChanProjectName,
                        sizeof(new_app_info.project_name)) != 0) {
                    ESP_LOGE(TAG, "Rejected non-StackChan XC Body firmware");
                    return fail_download();
                }
                std::string embedded_version(
                    new_app_info.version,
                    strnlen(
                        new_app_info.version,
                        sizeof(new_app_info.version)));
                if (embedded_version != expected_version) {
                    ESP_LOGE(
                        TAG,
                        "Firmware version mismatch: image=%s manifest=%s",
                        embedded_version.c_str(),
                        expected_version.c_str());
                    return fail_download();
                }

                RecordOtaStage(OtaDiagnosticStage::kBegin, total_read);
                if (esp_ota_begin(
                        update_partition,
                        expected_size,
                        &update_handle) != ESP_OK) {
                    ESP_LOGE(TAG, "Failed to begin OTA");
                    return fail_download();
                }

                update_started = true;
                image_header_checked = true;
                std::string().swap(image_header);
            }
        }

        // Write to flash when buffer is full (4KB) or it's the last chunk
        bool is_last_chunk = (ret == 0);
        if (image_header_checked &&
            (buffer_offset == PAGE_SIZE ||
             (is_last_chunk && buffer_offset > 0))) {
            RecordOtaStage(OtaDiagnosticStage::kWrite, total_read);
            auto err = esp_ota_write(update_handle, buffer, buffer_offset);
            if (err != ESP_OK) {
                ESP_LOGE(TAG, "Failed to write OTA data: %s", esp_err_to_name(err));
                return fail_download();
            }

            buffer_offset = 0;
        }

        if (is_last_chunk) {
            break;
        }
    }

    RecordOtaStage(OtaDiagnosticStage::kVerify, total_read);
    if (!image_header_checked || total_read != expected_size) {
        ESP_LOGE(TAG, "Firmware download was incomplete");
        return fail_download();
    }

    std::array<unsigned char, 32> digest;
    if (!sha256.Finish(digest) || Sha256Hex(digest) != expected_sha256) {
        ESP_LOGE(TAG, "Firmware SHA-256 did not match manifest");
        return fail_download();
    }

    http->Close();
    heap_caps_free(buffer);

    esp_err_t err = esp_ota_end(update_handle);
    update_started = false;
    if (err != ESP_OK) {
        if (err == ESP_ERR_OTA_VALIDATE_FAILED) {
            ESP_LOGE(TAG, "Image validation failed, image is corrupted");
        } else {
            ESP_LOGE(TAG, "Failed to end OTA: %s", esp_err_to_name(err));
        }
        return false;
    }

    {
        Settings settings(kOtaSettingsNamespace, true);
        settings.SetString(kPendingVersionKey, expected_version);
        settings.SetString(kPendingSlotKey, update_partition->label);
    }

    RecordOtaStage(OtaDiagnosticStage::kActivate, total_read);
    err = esp_ota_set_boot_partition(update_partition);
    if (err != ESP_OK) {
        Settings settings(kOtaSettingsNamespace, true);
        ClearPendingUpdate(settings);
        ESP_LOGE(TAG, "Failed to set boot partition: %s", esp_err_to_name(err));
        return false;
    }

    ESP_LOGI(TAG, "Firmware upgrade successful");
    diagnostic_attempt.Complete();
    return true;
}

bool Ota::StartUpgrade(std::function<void(int progress, size_t speed)> callback) {
    return Upgrade(
        firmware_url_,
        firmware_version_,
        firmware_sha256_,
        firmware_size_,
        callback);
}


std::string Ota::GetActivationPayload() {
    if (!has_serial_number_) {
        return "{}";
    }

    std::string hmac_hex;
#ifdef SOC_HMAC_SUPPORTED
    uint8_t hmac_result[32]; // SHA-256 输出为32字节
    
    // 使用Key0计算HMAC
    esp_err_t ret = esp_hmac_calculate(HMAC_KEY0, (uint8_t*)activation_challenge_.data(), activation_challenge_.size(), hmac_result);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "HMAC calculation failed: %s", esp_err_to_name(ret));
        return "{}";
    }

    for (size_t i = 0; i < sizeof(hmac_result); i++) {
        char buffer[3];
        sprintf(buffer, "%02x", hmac_result[i]);
        hmac_hex += buffer;
    }
#endif

    cJSON *payload = cJSON_CreateObject();
    cJSON_AddStringToObject(payload, "algorithm", "hmac-sha256");
    cJSON_AddStringToObject(payload, "serial_number", serial_number_.c_str());
    cJSON_AddStringToObject(payload, "challenge", activation_challenge_.c_str());
    cJSON_AddStringToObject(payload, "hmac", hmac_hex.c_str());
    auto json_str = cJSON_PrintUnformatted(payload);
    std::string json(json_str);
    cJSON_free(json_str);
    cJSON_Delete(payload);

    ESP_LOGI(TAG, "Activation payload: %s", json.c_str());
    return json;
}

esp_err_t Ota::Activate() {
    if (!has_activation_challenge_) {
        ESP_LOGW(TAG, "No activation challenge found");
        return ESP_FAIL;
    }

    std::string url = GetCheckVersionUrl();
    if (url.back() != '/') {
        url += "/activate";
    } else {
        url += "activate";
    }

    auto http = SetupHttp();

    std::string data = GetActivationPayload();
    http->SetContent(std::move(data));

    if (!http->Open("POST", url)) {
        ESP_LOGE(TAG, "Failed to open HTTP connection");
        return ESP_FAIL;
    }
    
    auto status_code = http->GetStatusCode();
    if (status_code == 202) {
        return ESP_ERR_TIMEOUT;
    }
    if (status_code != 200) {
        ESP_LOGE(TAG, "Failed to activate, code: %d, body: %s", status_code, http->ReadAll().c_str());
        return ESP_FAIL;
    }

    ESP_LOGI(TAG, "Activation successful");
    return ESP_OK;
}
