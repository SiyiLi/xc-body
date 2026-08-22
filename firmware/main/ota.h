#ifndef _OTA_H
#define _OTA_H

#include <cstddef>
#include <functional>
#include <string>

#include <esp_err.h>
#include "board.h"

struct OtaPolicyStatus {
    bool automatic_updates_enabled = true;
    std::string pending_version;
    std::string failed_version;
    int rollback_reset_reason = 0;
};

struct OtaDownloadDiagnostics {
    std::string state = "none";
    std::string stage = "none";
    size_t bytes_received = 0;
    size_t expected_size = 0;
    int progress = 0;
    bool has_previous_interruption = false;
    std::string previous_stage = "none";
    size_t previous_bytes_received = 0;
    size_t previous_expected_size = 0;
    int previous_reset_reason = 0;
};

class Ota {
public:
    Ota();
    ~Ota();

    esp_err_t CheckVersion();
    esp_err_t Activate();
    bool HasActivationChallenge() { return has_activation_challenge_; }
    bool HasNewVersion() { return has_new_version_; }
    bool HasMqttConfig() { return has_mqtt_config_; }
    bool HasWebsocketConfig() { return has_websocket_config_; }
    bool HasActivationCode() { return has_activation_code_; }
    bool HasServerTime() { return has_server_time_; }
    bool StartUpgrade(std::function<void(int progress, size_t speed)> callback);
    static bool Upgrade(
        const std::string& firmware_url,
        const std::string& expected_version,
        const std::string& expected_sha256,
        size_t expected_size,
        std::function<void(int progress, size_t speed)> callback);
    static OtaPolicyStatus GetPolicyStatus();
    static OtaDownloadDiagnostics GetDownloadDiagnostics();
    static void SetAutomaticUpdatesEnabled(bool enabled);
    static void RecordRollbackIfNeeded();
    static void MarkCurrentVersionValid();

    const std::string& GetFirmwareVersion() const { return firmware_version_; }
    const std::string& GetCurrentVersion() const { return current_version_; }
    const std::string& GetFirmwareUrl() const { return firmware_url_; }
    const std::string& GetFirmwareSha256() const { return firmware_sha256_; }
    size_t GetFirmwareSize() const { return firmware_size_; }
    bool HasAssetsForCurrentVersion() const {
        return has_assets_ && assets_version_ == current_version_;
    }
    const std::string& GetAssetsVersion() const { return assets_version_; }
    const std::string& GetAssetsUrl() const { return assets_url_; }
    const std::string& GetAssetsSha256() const { return assets_sha256_; }
    size_t GetAssetsSize() const { return assets_size_; }
    const std::string& GetActivationMessage() const { return activation_message_; }
    const std::string& GetActivationCode() const { return activation_code_; }
    std::string GetCheckVersionUrl();

private:
    std::string activation_message_;
    std::string activation_code_;
    bool has_new_version_ = false;
    bool has_mqtt_config_ = false;
    bool has_websocket_config_ = false;
    bool has_server_time_ = false;
    bool has_activation_code_ = false;
    bool has_serial_number_ = false;
    bool has_activation_challenge_ = false;
    std::string current_version_;
    std::string firmware_version_;
    std::string firmware_url_;
    std::string firmware_sha256_;
    size_t firmware_size_ = 0;
    bool has_assets_ = false;
    std::string assets_version_;
    std::string assets_url_;
    std::string assets_sha256_;
    size_t assets_size_ = 0;
    std::string activation_challenge_;
    std::string serial_number_;
    int activation_timeout_ms_ = 30000;

    std::function<void(int progress, size_t speed)> upgrade_callback_;
    std::string GetActivationPayload();
    std::unique_ptr<Http> SetupHttp();
};

#endif // _OTA_H
