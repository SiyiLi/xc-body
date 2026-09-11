#include "wifi_board.h"
#include "cores3_audio_codec.h"
#include "display/lcd_display.h"
#include "display/lvgl_display/gif/lvgl_gif.h"
#include "application.h"
#include "config.h"
#include "power_save_timer.h"
#include "i2c_device.h"
#include "axp2101.h"
#include "mcp_server.h"
#include "ota.h"
#include "settings.h"
#include "led_strip.h"
#include "feetech_scs.h"
using ScsBus = FeetechScs;
// FeetechScs::WritePos returns 0 on ACK and -1 on bus error.
static inline bool ServoWritePosOk(int r) { return r >= 0; }
#include "assets.h"
#include "usb_control.h"

#include <smooth_ui_toolkit.hpp>
#include <esp_log.h>
#include <esp_wifi.h>
#include <driver/i2c_master.h>
#include <driver/gpio.h>
#include <driver/uart.h>
#include <esp_lcd_panel_io.h>
#include <esp_lcd_panel_ops.h>
#include <esp_lcd_ili9341.h>
#include <esp_heap_caps.h>
#include <esp_timer.h>
#include <esp_random.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <freertos/semphr.h>
#include "esp_video.h"
#include <cJSON.h>
#include <lvgl.h>
#include <algorithm>
#include <array>
#include <atomic>
#include <cmath>
#include <cstdio>
#include <limits>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <string>
#include <ctime>
#include <cstdlib>
#include <sys/time.h>
#include <utility>
#include <vector>

#define TAG "StackChanBoard"

namespace {

constexpr char kPublicIpLocationUrl[] =
    "https://ipwho.is/?fields=success,latitude,longitude";
constexpr char kScreenSaverDigitsFontAsset[] =
    "screensaver-digits.cbin";
constexpr char kScreenSaverWeatherFontAsset[] =
    "screensaver-weather.cbin";
constexpr char kScreenSaverDateFontAsset[] = "screensaver-date.cbin";
constexpr size_t kScreenSaverWeatherIconCount = 20;
constexpr size_t kScreenSaverWeatherIconBytes = 80 * 64 * 3;
constexpr std::array<const char*, kScreenSaverWeatherIconCount>
    kScreenSaverWeatherIconAssets = {
        "w-clear-day.rgb565a8",
        "w-clear-night.rgb565a8",
        "w-wind.rgb565a8",
        "w-partly-day.rgb565a8",
        "w-partly-night.rgb565a8",
        "w-overcast.rgb565a8",
        "w-shower-day.rgb565a8",
        "w-shower-night.rgb565a8",
        "w-thunder-rain.rgb565a8",
        "w-hail.rgb565a8",
        "w-light-rain.rgb565a8",
        "w-heavy-rain.rgb565a8",
        "w-snow.rgb565a8",
        "w-sleet.rgb565a8",
        "w-fog.rgb565a8",
        "w-haze.rgb565a8",
        "w-dust.rgb565a8",
        "w-hot.rgb565a8",
        "w-cold.rgb565a8",
        "w-unknown.rgb565a8",
    };


struct LvglBinFontDeleter {
    void operator()(lv_font_t* font) const {
        lv_binfont_destroy(font);
    }
};

using LvglBinFontPtr = std::unique_ptr<lv_font_t, LvglBinFontDeleter>;

}  // namespace

class Pmic : public Axp2101 {
public:
    // Power Init
    Pmic(i2c_master_bus_handle_t i2c_bus, uint8_t addr) : Axp2101(i2c_bus, addr) {
        uint8_t data = ReadReg(0x90);
        data |= 0b10110100;
        WriteReg(0x90, data);
        WriteReg(0x99, (0b11110 - 5));
        WriteReg(0x97, (0b11110 - 2));
        WriteReg(0x69, 0b00110101);
        WriteReg(0x30, 0b111111);
        WriteReg(0x90, 0xBF);
        WriteReg(0x94, 33 - 5);
        WriteReg(0x95, 33 - 5);
    }

    void SetBrightness(uint8_t brightness) {
        brightness = ((brightness + 641) >> 5);
        WriteReg(0x99, brightness);
    }
};

class CustomBacklight : public Backlight {
public:
    CustomBacklight(Pmic *pmic) : pmic_(pmic) {}

    void SetBrightnessImpl(uint8_t brightness) override {
        pmic_->SetBrightness(target_brightness_);
        brightness_ = target_brightness_;
    }

private:
    Pmic *pmic_;
};

class Aw9523 : public I2cDevice {
public:
    // Exanpd IO Init
    Aw9523(i2c_master_bus_handle_t i2c_bus, uint8_t addr) : I2cDevice(i2c_bus, addr) {
        WriteReg(0x02, 0b00000111);  // P0
        WriteReg(0x03, 0b10001111);  // P1
        WriteReg(0x04, 0b00011000);  // CONFIG_P0
        WriteReg(0x05, 0b00001100);  // CONFIG_P1
        WriteReg(0x11, 0b00010000);  // GCR P0 port is Push-Pull mode.
        WriteReg(0x12, 0b11111111);  // LEDMODE_P0
        WriteReg(0x13, 0b11111111);  // LEDMODE_P1
    }

    void ResetAw88298() {
        ESP_LOGI(TAG, "Reset AW88298");
        WriteReg(0x02, 0b00000011);
        vTaskDelay(pdMS_TO_TICKS(10));
        WriteReg(0x02, 0b00000111);
        vTaskDelay(pdMS_TO_TICKS(50));
    }

    void ResetIli9342() {
        ESP_LOGI(TAG, "Reset IlI9342");
        WriteReg(0x03, 0b10000001);
        vTaskDelay(pdMS_TO_TICKS(20));
        WriteReg(0x03, 0b10000011);
        vTaskDelay(pdMS_TO_TICKS(10));
    }
};

class Ft6336 : public I2cDevice {
public:
    struct TouchPoint_t {
        int num = 0;
        int x = -1;
        int y = -1;
    };
    
    Ft6336(i2c_master_bus_handle_t i2c_bus, uint8_t addr) : I2cDevice(i2c_bus, addr) {
        uint8_t chip_id = ReadReg(0xA3);
        ESP_LOGI(TAG, "Get chip ID: 0x%02X", chip_id);
        read_buffer_ = new uint8_t[6];
    }

    ~Ft6336() {
        delete[] read_buffer_;
    }

    void UpdateTouchPoint() {
        ReadRegs(0x02, read_buffer_, 6);
        tp_.num = read_buffer_[0] & 0x0F;
        tp_.x = ((read_buffer_[1] & 0x0F) << 8) | read_buffer_[2];
        tp_.y = ((read_buffer_[3] & 0x0F) << 8) | read_buffer_[4];
    }

    inline const TouchPoint_t& GetTouchPoint() {
        return tp_;
    }

private:
    uint8_t* read_buffer_ = nullptr;
    TouchPoint_t tp_;
};

// Minimal PY32 IO Expander driver (servo power switch on pin 0 / VM EN).
// Ported from M5Stack-BSP PY32IOExpander.cpp.
//
// IMPORTANT: this class deliberately does NOT inherit from I2cDevice.
// The base I2cDevice registers every device at scl_speed_hz = 400 kHz, but
// the M5 reference implementation (`PY32IOExpander_Class`) defaults to
// 100 kHz, and 400 kHz appears to leave PY32 in a half-finished slave
// state — `i2c_master_probe` returns ACK but the very next
// `i2c_master_transmit_receive` for REG_VERSION times out (0x103) every
// time. We register our own i2c_master device handle at 100 kHz to match
// the M5 default. Other peripherals on the bus (Si12T at 0x68, AXP2101,
// AW9523, FT6336) keep using the 400 kHz path through I2cDevice.
//
// Reliability notes:
//  - Each I2C op transparently retries up to I2C_INNER_RETRIES on transient
//    errors, with a short vTaskDelay between attempts.
//  - All bit-level write helpers propagate success as bool so the caller
//    can decide whether the GPIO actually got configured.
//  - Begin() can optionally return the version byte so the caller can log
//    which attempt finally talked to the chip.
class Py32IoExpander {
public:
    static constexpr uint8_t  DEFAULT_ADDR = 0x6F;
    static constexpr uint32_t I2C_FREQ_HZ  = 100000;  // 100 kHz (M5 default)
    static constexpr uint8_t  REG_GPIO_O_L_PUBLIC = 0x05;  // exposed for verify

    Py32IoExpander(i2c_master_bus_handle_t i2c_bus, uint8_t addr = DEFAULT_ADDR) {
        i2c_device_config_t cfg = {
            .dev_addr_length = I2C_ADDR_BIT_LEN_7,
            .device_address  = addr,
            .scl_speed_hz    = I2C_FREQ_HZ,
            .scl_wait_us     = 0,
            .flags           = { .disable_ack_check = 0 },
        };
        ESP_ERROR_CHECK(i2c_master_bus_add_device(i2c_bus, &cfg, &i2c_device_));
    }

    // Probe the chip. On success, returns true and (if non-null) writes the
    // version byte to out_version. Internal reads use SafeReadReg, which
    // already retries on transient I2C errors — so the chip is genuinely
    // unreachable / not yet ready when this returns false.
    bool Begin(uint8_t* out_version = nullptr) {
        uint8_t version = 0;
        if (!SafeReadReg(REG_VERSION, &version)) {
            return false;
        }
        if (version == 0x00 || version == 0xFF) {
            return false;
        }
        if (out_version != nullptr) {
            *out_version = version;
        }
        return true;
    }

    // direction: false=input, true=output. Accepts pin 0..15 (PY32 has 14
    // GPIOs; the WS2812 data line is on pin 13, in the high byte).
    bool SetDirection(uint8_t pin, bool output) {
        return WriteBitWideSafe(REG_GPIO_M_L, REG_GPIO_M_H, pin, output);
    }

    // mode: false=pull down, true=pull up. Accepts pin 0..15.
    bool SetPullMode(uint8_t pin, bool up) {
        if (up) {
            bool a = WriteBitWideSafe(REG_GPIO_PD_L, REG_GPIO_PD_H, pin, false);
            bool b = WriteBitWideSafe(REG_GPIO_PU_L, REG_GPIO_PU_H, pin, true);
            return a && b;
        } else {
            bool a = WriteBitWideSafe(REG_GPIO_PU_L, REG_GPIO_PU_H, pin, false);
            bool b = WriteBitWideSafe(REG_GPIO_PD_L, REG_GPIO_PD_H, pin, true);
            return a && b;
        }
    }

    bool DigitalWrite(uint8_t pin, bool level) {
        return WriteBitSafe(REG_GPIO_O_L, pin, level);
    }

    // Read back the current output low-byte register (pins 0..7) for
    // verification after DigitalWrite. Returns false if the read failed.
    bool ReadOutputLow(uint8_t* out) {
        return SafeReadReg(REG_GPIO_O_L, out);
    }

    // Drive mode for any pin (0..15). false=push-pull, true=open-drain.
    // The WS2812 data line on pin 13 must be push-pull.
    bool SetDriveMode(uint8_t pin, bool open_drain) {
        return WriteBitWideSafe(REG_GPIO_DRV_L, REG_GPIO_DRV_H, pin, open_drain);
    }

    // ---- LED (WS2812 driven by the PY32 itself, data line on pin 13) ----
    // REG_LED_CFG packs both the LED count (bits 0-5, max 32) and the latch
    // trigger (bit 6). Writing the count clears bit 6, which is fine because
    // it's a self-clearing strobe. RefreshLeds() does read-modify-write so
    // the count is preserved when we latch.
    bool SetLedCount(uint8_t count) {
        if (count > 32) count = 32;
        return SafeWriteReg(REG_LED_CFG, count & 0x3F);
    }

    // Set one LED to RGB888. RGB888 → RGB565 packing matches the M5 BSP:
    // ((r&0xF8)<<8) | ((g&0xFC)<<3) | (b>>3), little-endian on the wire.
    // Does NOT latch — call RefreshLeds() once after a batch of updates.
    bool SetLedColor(uint8_t index, uint8_t r, uint8_t g, uint8_t b) {
        if (index >= 32) return false;
        uint16_t v = (uint16_t)(((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3));
        uint8_t buf[3] = { (uint8_t)(REG_LED_RAM_START + index * 2),
                           (uint8_t)(v & 0xFF),
                           (uint8_t)((v >> 8) & 0xFF) };
        return SafeWriteRaw(buf, sizeof(buf));
    }

    // Burst-write up to N LED RGB565 pairs starting at index 0. data is
    // packed { lo0, hi0, lo1, hi1, ... } and len is the byte count
    // (=2*num_leds, max 64). Single I2C transaction — much faster than
    // calling SetLedColor in a loop. Does NOT latch.
    bool SetLedData(const uint8_t* data, size_t len) {
        if (data == nullptr || len == 0) return false;
        if (len > 64) len = 64;
        uint8_t buf[1 + 64];
        buf[0] = REG_LED_RAM_START;
        for (size_t i = 0; i < len; i++) buf[1 + i] = data[i];
        return SafeWriteRaw(buf, 1 + len);
    }

    // Latch the LED RAM out to the WS2812 strip. Read-modify-write so the
    // current count (bits 0-5) is preserved alongside the latch bit (bit 6).
    bool RefreshLeds() {
        uint8_t cfg = 0;
        if (!SafeReadReg(REG_LED_CFG, &cfg)) return false;
        return SafeWriteReg(REG_LED_CFG, (uint8_t)(cfg | (1u << 6)));
    }

private:
    // Owned device handle (NOT inherited from I2cDevice — see class comment).
    // Registered at 100 kHz in the constructor so this transport runs slower
    // than the rest of the bus.
    i2c_master_dev_handle_t i2c_device_ = nullptr;

    static constexpr uint8_t REG_VERSION  = 0x02;
    static constexpr uint8_t REG_GPIO_M_L = 0x03;  // Direction (mode) low byte
    static constexpr uint8_t REG_GPIO_M_H = 0x04;  // Direction (mode) high byte
    static constexpr uint8_t REG_GPIO_O_L = 0x05;  // Output low byte
    static constexpr uint8_t REG_GPIO_O_H = 0x06;  // Output high byte
    static constexpr uint8_t REG_GPIO_PU_L = 0x09; // Pull-up low byte
    static constexpr uint8_t REG_GPIO_PU_H = 0x0A; // Pull-up high byte
    static constexpr uint8_t REG_GPIO_PD_L = 0x0B; // Pull-down low byte
    static constexpr uint8_t REG_GPIO_PD_H = 0x0C; // Pull-down high byte
    static constexpr uint8_t REG_GPIO_DRV_L = 0x13; // Drive mode low byte
    static constexpr uint8_t REG_GPIO_DRV_H = 0x14; // Drive mode high byte
    static constexpr uint8_t REG_LED_CFG       = 0x24;  // count[5:0] + latch[6]
    static constexpr uint8_t REG_LED_RAM_START = 0x30;  // 2 bytes per LED, RGB565 LE

    // I2C op transient-retry parameters. Total budget per failed op is
    // (I2C_INNER_RETRIES - 1) * I2C_RETRY_DELAY_MS, i.e. ~30 ms here.
    static constexpr int I2C_INNER_RETRIES  = 3;
    static constexpr int I2C_RETRY_DELAY_MS = 15;

    // Safe I2C read — retries up to I2C_INNER_RETRIES on transient errors,
    // logs WARN only on the final failure to keep the log readable.
    bool SafeReadReg(uint8_t reg, uint8_t* out) {
        esp_err_t err = ESP_FAIL;
        for (int i = 0; i < I2C_INNER_RETRIES; i++) {
            err = i2c_master_transmit_receive(i2c_device_, &reg, 1, out, 1, 100);
            if (err == ESP_OK) {
                return true;
            }
            if (i + 1 < I2C_INNER_RETRIES) {
                vTaskDelay(pdMS_TO_TICKS(I2C_RETRY_DELAY_MS));
            }
        }
        ESP_LOGW("Py32IoExpander", "I2C read reg 0x%02X failed after %d tries: 0x%X",
                 reg, I2C_INNER_RETRIES, err);
        return false;
    }

    // Safe I2C write — same retry semantics as SafeReadReg.
    bool SafeWriteReg(uint8_t reg, uint8_t value) {
        uint8_t buffer[2] = {reg, value};
        esp_err_t err = ESP_FAIL;
        for (int i = 0; i < I2C_INNER_RETRIES; i++) {
            err = i2c_master_transmit(i2c_device_, buffer, 2, 100);
            if (err == ESP_OK) {
                return true;
            }
            if (i + 1 < I2C_INNER_RETRIES) {
                vTaskDelay(pdMS_TO_TICKS(I2C_RETRY_DELAY_MS));
            }
        }
        ESP_LOGW("Py32IoExpander", "I2C write reg 0x%02X failed after %d tries: 0x%X",
                 reg, I2C_INNER_RETRIES, err);
        return false;
    }

    // Read-Modify-Write a single bit using safe I2C. Pin 0..7 only.
    // Returns false if either the read or the write step ultimately failed.
    bool WriteBitSafe(uint8_t reg, uint8_t pin, bool value) {
        if (pin >= 8) {
            return false;
        }
        uint8_t v = 0;
        if (!SafeReadReg(reg, &v)) {
            return false;
        }
        if (value) {
            v |= (uint8_t)(1u << pin);
        } else {
            v &= (uint8_t)~(1u << pin);
        }
        return SafeWriteReg(reg, v);
    }

    // 16-bit RMW: pin 0..7 -> reg_l, pin 8..15 -> reg_h. Used for any pin
    // beyond pin 7 (LED data line is on pin 13, so all the LED setup goes
    // through this path).
    bool WriteBitWideSafe(uint8_t reg_l, uint8_t reg_h, uint8_t pin, bool value) {
        if (pin >= 16) return false;
        uint8_t reg = (pin < 8) ? reg_l : reg_h;
        uint8_t bit = (uint8_t)(pin & 0x07);
        uint8_t v = 0;
        if (!SafeReadReg(reg, &v)) return false;
        if (value) v |= (uint8_t)(1u << bit);
        else       v &= (uint8_t)~(1u << bit);
        return SafeWriteReg(reg, v);
    }

    // Burst write: ship a pre-built {reg, ...payload} buffer in a single
    // i2c_master_transmit. Used by the LED RAM writes which would otherwise
    // require dozens of individual register writes. Same retry semantics
    // as SafeWriteReg.
    bool SafeWriteRaw(const uint8_t* buf, size_t len) {
        esp_err_t err = ESP_FAIL;
        for (int i = 0; i < I2C_INNER_RETRIES; i++) {
            err = i2c_master_transmit(i2c_device_, buf, len, 100);
            if (err == ESP_OK) {
                return true;
            }
            if (i + 1 < I2C_INNER_RETRIES) {
                vTaskDelay(pdMS_TO_TICKS(I2C_RETRY_DELAY_MS));
            }
        }
        ESP_LOGW("Py32IoExpander", "I2C raw write (len=%u) failed after %d tries: 0x%X",
                 (unsigned)len, I2C_INNER_RETRIES, err);
        return false;
    }
};

// Minimal Si12T driver (12-channel capacitive touch sensor, TSM12-compatible).
// Used for the StackChan head-stroke / head-tap detection. Only the read path
// (Output1 register, channels 1-4) is needed for Phase 7. We expose just the
// first three channels through ReadTouchState() because the StackChan head
// has 3 conductive zones wired to TS1..TS3.
//
// Datasheet excerpt:
//   - I2C 7-bit address: 0xD0 >> 1 == 0x68 when ID_SEL pin is tied to GND.
//     This matches the address probed at boot ("0x68").
//   - Reset value of CTRL (0x09) is 0b00000111 (SLEEP=1). We must clear SLEEP
//     to enter normal sensing mode: CTRL = 0b00000011.
//   - Output1 (0x10) packs four channels into one byte (2 bits per channel):
//       bit[1:0] = OUT1, bit[3:2] = OUT2, bit[5:4] = OUT3, bit[7:6] = OUT4
//       00 = no output, 01 = low, 10 = medium, 11 = high.
//   - There is no dedicated chip-id register, so Begin() validates the device
//     via successful I2C ACK on the CTRL read + non-0xFF Output1 read.
class Si12T : public I2cDevice {
public:
    static constexpr uint8_t DEFAULT_ADDR = 0x68;  // ID_SEL = GND

    struct TouchState {
        bool zone[3];          // CH1, CH2, CH3 — true if any output level set
        uint8_t output1_raw;   // raw Output1 register byte (0x10)
        bool ok;               // false if the I2C read failed
    };

    Si12T(i2c_master_bus_handle_t i2c_bus, uint8_t addr = DEFAULT_ADDR)
        : I2cDevice(i2c_bus, addr) {}

    // Probe the chip and bring it out of sleep. Returns true on success.
    bool Begin() {
        uint8_t ctrl = 0;
        if (!SafeReadReg(REG_CTRL, &ctrl)) {
            return false;
        }
        // CTRL bit1 = SLEEP. Clear it; bit1:0 must hold 1 per datasheet
        // ("CTRL Bit1, Bit0 = 1 1" reset value), so write 0b00000011.
        if (!SafeWriteReg(REG_CTRL, 0x03)) {
            return false;
        }
        // Verify the device actually responds on the output register.
        // 0xFF would indicate an open bus / no device.
        uint8_t out1 = 0;
        if (!SafeReadReg(REG_OUTPUT1, &out1)) {
            return false;
        }
        if (out1 == 0xFF) {
            ESP_LOGW("Si12T", "Output1 read 0xFF (likely no device)");
            return false;
        }
        ESP_LOGI("Si12T", "init OK: ctrl=0x%02X out1=0x%02X (sleep cleared)", ctrl, out1);
        return true;
    }

    // Sample channels CH1..CH3 from Output1 (0x10). Single-shot read; the
    // caller is expected to debounce / interpret duration externally.
    TouchState ReadTouchState() {
        TouchState s = {};
        s.ok = false;
        if (!SafeReadReg(REG_OUTPUT1, &s.output1_raw)) {
            return s;
        }
        s.ok = true;
        // Each channel uses 2 bits; nonzero = touched at some level.
        s.zone[0] = ((s.output1_raw >> 0) & 0x3) != 0;  // CH1
        s.zone[1] = ((s.output1_raw >> 2) & 0x3) != 0;  // CH2
        s.zone[2] = ((s.output1_raw >> 4) & 0x3) != 0;  // CH3
        return s;
    }

private:
    static constexpr uint8_t REG_CTRL    = 0x09;  // CTRL, SLEEP bit etc.
    static constexpr uint8_t REG_OUTPUT1 = 0x10;  // CH1..CH4 packed (2bpp)

    bool SafeReadReg(uint8_t reg, uint8_t* out) {
        esp_err_t err = i2c_master_transmit_receive(i2c_device_, &reg, 1, out, 1, 100);
        if (err != ESP_OK) {
            ESP_LOGW("Si12T", "I2C read reg 0x%02X failed: 0x%X", reg, err);
            return false;
        }
        return true;
    }

    bool SafeWriteReg(uint8_t reg, uint8_t value) {
        uint8_t buffer[2] = {reg, value};
        esp_err_t err = i2c_master_transmit(i2c_device_, buffer, 2, 100);
        if (err != ESP_OK) {
            ESP_LOGW("Si12T", "I2C write reg 0x%02X failed: 0x%X", reg, err);
            return false;
        }
        return true;
    }
};

class StackChanBoard : public WifiBoard, public StackChanExpressionController {
private:
    // Internal I2C bus (shared by AXP2101 / AW9523 / FT6336 / PY32 / Si12T /
    // audio codec / IMU). Direct on-board ICs only; not exposed through
    // self.i2c.* MCP tools.
    i2c_master_bus_handle_t i2c_bus_;
    // External I2C bus dedicated to Grove Port A. Exposed through self.i2c.*
    // MCP tools so the gateway can drive attached M5Stack Unit modules.
    i2c_master_bus_handle_t port_a_i2c_bus_;
    // Port B WS2812 generic strip state (driven from MCP tools self.port_b.ws2812.*).
    // Independent from the on-board PY32-driven 12-LED base strip (self.led.*),
    // which uses I2C -> PY32 internal WS2812 engine. The two paths share no
    // hardware peripheral and no software state; existing self.led.* behaviour
    // is byte-for-byte unchanged.
    bool ws2812_ok_ = false;
    uint16_t ws2812_led_count_ = 0;
    led_strip_handle_t ws2812_handle_ = nullptr;
    static constexpr gpio_num_t PORT_B_WS2812_DATA_PIN = GPIO_NUM_9;  // CoreS3 HY2.0-4P (Port B) digital OUTPUT
    static constexpr uint16_t PORT_B_WS2812_MAX_LEDS = 256;
    // Port C WS2812 generic strip state (driven from MCP tools self.port_c.ws2812.*).
    bool port_c_ws2812_ok_ = false;
    uint16_t port_c_ws2812_led_count_ = 0;
    led_strip_handle_t port_c_ws2812_handle_ = nullptr;
    static constexpr gpio_num_t PORT_C_WS2812_DATA_PIN = GPIO_NUM_17;  // CoreS3 HY2.0-4P (Port C) signal 1
    static constexpr uint16_t PORT_C_WS2812_MAX_LEDS = 256;
    Pmic* pmic_;
    Aw9523* aw9523_;
    Ft6336* ft6336_;
    LcdDisplay* display_;
    EspVideo* camera_;
    esp_timer_handle_t touchpad_timer_;
    PowerSaveTimer* power_save_timer_;
    ScsBus scs_bus_;
    std::unique_ptr<Py32IoExpander> io_expander_;

    lv_obj_t* face_image_ = nullptr;
    lv_img_dsc_t face_gif_source_ = {};
    std::unique_ptr<LvglGif> face_gif_;

    lv_obj_t* settings_panel_ = nullptr;
    lv_obj_t* settings_volume_label_ = nullptr;
    std::atomic<bool> settings_open_{false};

    // Milestone 4 idle clock/weather overlay. It is an ordinary LVGL layer;
    // display dimming and sleep remain owned by PowerSaveTimer.
    lv_obj_t* screensaver_ = nullptr;
    lv_obj_t* screensaver_hour_ = nullptr;
    lv_obj_t* screensaver_minute_ = nullptr;
    lv_obj_t* screensaver_weather_icon_ = nullptr;
    lv_obj_t* screensaver_weather_caption_ = nullptr;
    lv_obj_t* screensaver_date_ = nullptr;
    lv_obj_t* screensaver_temperature_ = nullptr;
    LvglBinFontPtr screensaver_digits_font_;
    LvglBinFontPtr screensaver_weather_font_;
    LvglBinFontPtr screensaver_date_font_;
    std::array<lv_image_dsc_t, kScreenSaverWeatherIconCount>
        screensaver_weather_icons_ = {};
    bool screensaver_resources_ready_ = false;
    std::atomic<bool> screensaver_visible_{false};
    std::atomic<int64_t> screensaver_last_activity_us_{0};
    std::atomic<bool> offer_pending_{false};
    // Guarded by the display lock.
    bool weather_available_ = false;
    int weather_icon_code_ = 999;
    int weather_temperature_c_ = 0;
    std::string weather_summary_;

    struct PublicIpLocation {
        bool available = false;
        double latitude = 0;
        double longitude = 0;
    };
    std::mutex public_ip_location_mutex_;
    std::string public_ip_location_ssid_;
    PublicIpLocation public_ip_location_;
    bool public_ip_location_task_running_ = false;

    static constexpr int64_t SCREEN_SAVER_IDLE_TIMEOUT_US =
        60LL * 1000 * 1000;

    // Phase 7: Si12T head-touch sensing.
    // Polling every TOUCH_POLL_MS samples Output1 (CH1..CH3 -> 3 head zones).
    // Edge detection on the OR of the three zones produces TAP / STROKE
    // gestures. Both play the locally configurable touch expression and
    // retain their distinct local event subtype.
    enum class TouchEvent : uint8_t {
        IDLE = 0,
        TAP,
        STROKE,
    };
    static constexpr int TOUCH_POLL_MS    = 100;  // 100 Hz polling
    static constexpr int STROKE_MIN_MS    = 400;  // was 600; lowered because
                                                  // finger-glide between zones
                                                  // and Si12T auto-recalibration
                                                  // inject brief "all-false"
                                                  // gaps that cut a real stroke
                                                  // short of 600 ms.
    static constexpr int COOLDOWN_MS      = 800;  // post-reaction noise gate
    // With 2-sample debounce this gives ~200 ms confirm latency, fast enough
    // to catch a quick "pon" (~200 ms press) while still rejecting single-
    // sample jitter. Was 200 ms polling -> 400 ms confirm, which silently
    // dropped most short taps.
    std::atomic<bool> touch_sensor_enabled_{true};
    std::unique_ptr<Si12T> si12t_;
    bool si12t_ok_ = false;
    esp_timer_handle_t touch_poll_timer_ = nullptr;

    // Touch detection state (single-thread access from the touch_poll_timer_
    // callback, which runs on the ESP_TIMER_TASK).
    bool touch_pressed_prev_ = false;          // last sample (debounced)
    bool touch_pressed_pending_ = false;       // candidate awaiting confirm
    int  touch_pending_count_ = 0;             // consecutive samples matching
    uint64_t touch_press_start_us_ = 0;        // when pressed_prev_ went true
    uint64_t cooldown_until_us_ = 0;           // ignore press until this ts
    bool head_touch_woke_screensaver_ = false;

    // Last reported event for MCP get_touch_state.
    TouchEvent last_event_ = TouchEvent::IDLE;
    uint64_t   last_event_us_ = 0;
    bool       last_zone_snapshot_[3] = {false, false, false};
    uint8_t    last_output1_raw_ = 0;
    // Press-start snapshot. last_* fields above are overwritten every poll
    // tick, so by the time HandleTap / HandleStroke fires on the falling edge
    // they reflect the release state (zones=000 raw=0x00). press_start_*
    // captures the rising-edge state so the log can show what the sensor
    // actually saw when the touch began. Useful for distinguishing genuine
    // touches (CH1〜CH3 set) from false positives (e.g. CH4 noise, raw=0x00
    // with press judged via debounce, etc.).
    bool       press_start_zones_[3] = {false, false, false};
    uint8_t    press_start_output1_raw_ = 0;

    enum class ExpressionStep : uint8_t {
        STARTING = 0,
        CENTERING,
        RUNNING_CURVE,
        PAUSING,
        WAITING_FOR_FACE,
        RESTORING_FACE,
        RECOVERING_TO_IDLE,
    };
    enum class PhysicalBehaviorOwner : uint8_t {
        IDLE = 0,
        EXPRESSION,
        RAW,
        MAINTENANCE_RESERVED,
        MAINTENANCE,
    };
    static constexpr int XC_BODY_IDLE_YAW_DEG = 0;
    static constexpr int XC_BODY_IDLE_PITCH_DEG = 43;
    static constexpr int XC_BODY_BEHAVIOR_SPEED_DPS = 30;
    static constexpr uint64_t EXPRESSION_STARTUP_TIMEOUT_US = 5000000ULL;
    static constexpr uint64_t EXPRESSION_FACE_DURATION_US = 2400000ULL;
    static constexpr uint64_t EXPRESSION_EXECUTION_MARGIN_US = 2000000ULL;
    static constexpr uint64_t EXPRESSION_RECOVERY_TIMEOUT_US = 5000000ULL;
    static constexpr uint64_t EXPRESSION_FACE_RESTORE_TIMEOUT_US = 500000ULL;
    static constexpr uint64_t EXPRESSION_RECOVERY_RETRY_INTERVAL_US =
        500000ULL;

    std::atomic<PhysicalBehaviorOwner> physical_behavior_owner_{
        PhysicalBehaviorOwner::IDLE};

    enum class ExpressionInvocation : uint8_t {
        PREVIEW = 0,
        BEHAVIOR,
        TOUCH,
    };

    std::atomic<bool> expression_active_{false};
    std::atomic<bool> expression_abort_requested_{false};
    std::atomic<ExpressionStep> expression_step_{
        ExpressionStep::STARTING};
    std::atomic<bool> expression_preview_result_ready_{false};
    std::atomic<StackChanExpressionOutcome> expression_preview_result_{
        StackChanExpressionOutcome::UNAVAILABLE};
    std::atomic<bool> physical_motion_unavailable_{false};
    StackChanExpressionRecipe expression_recipe_;
    size_t expression_step_index_ = 0;
    uint64_t expression_startup_deadline_us_ = 0;
    uint64_t expression_execution_deadline_us_ = 0;
    uint64_t expression_hold_until_us_ = 0;
    uint64_t expression_recovery_deadline_us_ = 0;
    uint64_t expression_recovery_retry_at_us_ = 0;
    uint64_t expression_face_restore_deadline_us_ = 0;
    StackChanExpressionOutcome expression_recovery_outcome_ =
        StackChanExpressionOutcome::MOTION_FAILED;
    StackChanExpressionOutcome expression_finish_outcome_ =
        StackChanExpressionOutcome::UNAVAILABLE;
    std::atomic<ExpressionInvocation> expression_invocation_{
        ExpressionInvocation::PREVIEW};
    std::string expression_name_;
    std::string expression_behavior_id_;
    const char* expression_behavior_success_subtype_ = nullptr;
    uint64_t expression_started_us_ = 0;

    // Shared motion state. This stays on the board singleton because boot-init
    // ReadPos restore / re-sync phases seed the same state before and after the
    // concrete MotionDriver is selected. Drivers only borrow these references.
    struct AxisMotion {
        int target_deg = 0;
        int start_deg = 0;
        int current_deg = 0;
        uint32_t move_start_ms = 0;
        // For the delegated driver: time the WritePos for this request
        // was successfully ACK'd by the servo (i.e. when the physical
        // motion actually began on the SCS0009's internal clock). May
        // be later than move_start_ms when the dispatch is delayed by
        // Tick wake or retry rounds. ApplyReadMoveResult's stuck-high
        // timeout is measured from dispatch_start_ms, not from staging,
        // so that degraded-bus latency does not cause premature force-
        // clear while the servo is genuinely still mid-motion. 0 means
        // "not yet dispatched"; ApplyReadMoveResult skips the
        // stuck-high check until dispatch_start_ms is populated by
        // FinishDispatch's write_ok branch. HostInterpolation path
        // does not consult this field.
        uint32_t dispatch_start_ms = 0;
        uint32_t move_duration_ms = 0;
        bool moving = false;
        // Monotonic counter incremented by ServoDelegatedMotionDriver::
        // StartMove on each dispatch stage. Used by Tick() to detect
        // when a newer StartMove has raced in between the snapshot at
        // the top of Tick() and the post-WritePos / post-ReadMove
        // commit (motion_mutex_ is dropped while the bus operation
        // runs). esp_timer_get_time()/1000 has 1 ms resolution and is
        // not unique enough on its own — two StartMove calls within
        // the same millisecond would collide on move_start_ms. The
        // HostInterpolation path does not consult this field; the
        // monotonic counter is owned by the delegated driver.
        uint64_t request_token = 0;
        // Set by the delegated driver when ApplyReadMoveResult's
        // 5-consecutive-failure force-clear fires: WritePos ACK
        // confirmed the servo received the command, but ReadMove
        // polling never observed completion. current_deg holds the
        // last optimistic commit (the requested target), but the
        // physical head may be mid-trajectory or at the wrong angle.
        // The next StartMove on this axis treats position_unknown as
        // a "force re-dispatch" signal (no-op skip is suppressed),
        // so a same-target retry surfaces the failure rather than
        // hiding it behind a stale-but-equal current_deg. The
        // HostInterpolation path does not consult this field.
        bool position_unknown = false;
    };
    class MotionDriver;
    // TODO: motion_mutex_/scs_bus_mutex_/servo_task_handle_ have no destroy path; board is singleton via DECLARE_BOARD.
    AxisMotion yaw_motion_;
    AxisMotion pitch_motion_;
    SemaphoreHandle_t motion_mutex_ = nullptr;     // protects AxisMotion fields
    SemaphoreHandle_t scs_bus_mutex_ = nullptr;    // serializes UART access (WritePos/ReadPos)
    std::unique_ptr<MotionDriver> motion_driver_;
    TaskHandle_t servo_task_handle_ = nullptr;
    uint32_t last_motion_end_ms_ = 0;              // ServoTask-private
    bool last_motion_end_valid_ = false;           // ServoTask-private
    std::atomic<bool> idle_timer_reset_pending_{false};
    enum class TorqueState : uint8_t {
        kEngaged = 0,
        kPartial = 1,
        kReleased = 2,
        kReleasing = 3,
        // Published when InternalSetServoTorque cannot confirm bus success
        // for both axes. Forward-progress invariant: the next motion / manual
        // call issues a real bus frame instead of short-circuiting. Mirrors
        // the WritePos retries-exhausted -> position_unknown convention for
        // the torque domain.
        kUncertain = 4,
    };
    std::atomic<TorqueState> torque_state_{TorqueState::kEngaged};
    std::atomic<uint32_t> torque_release_epoch_{0};
    // Per-axis commanded torque state is protected by scs_bus_mutex_;
    // torque_state_ publishes the derived cross-task summary.
    bool yaw_torque_enabled_ = true;               // protected by scs_bus_mutex_
    bool pitch_torque_enabled_ = true;             // protected by scs_bus_mutex_
    std::atomic<bool> boot_init_done_{false};
#if CONFIG_STACKCHAN_AUTO_TORQUE_RELEASE_ENABLED
    std::atomic<bool> auto_release_enabled_{true};
#else
    std::atomic<bool> auto_release_enabled_{false};
#endif
    static constexpr uint32_t MOTION_TICK_MS = 20;
    static constexpr uint32_t MOTION_DEFAULT_DURATION_MS = 600;
    // Speed-based motion API (Issue #129).
    // MIN_STEP_SAFE_SPEED_DPS prevents the raw-integer speed_dps escape hatch
    // from advancing less than one SCS0009 step per ServoTask tick. The
    // physical step is 300 deg / 1024 = 0.293 deg; at MOTION_TICK_MS=20 ms
    // this is 14.65 deg/s, rounded up to 15 deg/s for headroom. This shares
    // the same physical origin as BOOT_INIT_TARGET_DEG_PER_SEC (#121/#141),
    // but stays separate because the boot path carries its own duration-floor
    // semantics. See the Issue #129 -> #134 stepped-motion observation lineage.
    static constexpr int MIN_STEP_SAFE_SPEED_DPS = 15;
    // MIN_SMOOTH_SPEED_DPS is the on-device measured smoothness floor
    // (5 step/tick "transition out", measured 2026-05-15). Speeds below
    // this look textured on SCS0009 at MOTION_TICK_MS=20 ms; the firmware
    // permits sub-floor speeds (logged with ESP_LOGW) so callers like the
    // gateway "low" preset (30 dps) can deliver deliberately slow motion.
    static constexpr int MIN_SMOOTH_SPEED_DPS = 72;
    // MAX_SPEED_DPS is the SCS0009 datasheet reliability test working speed
    // (60 deg / 0.25 s = 240 deg/s, validated for >50k cycles at 1/2 rated load).
    static constexpr int MAX_SPEED_DPS = 240;
    // DEFAULT_SPEED_DPS is used when the caller passes speed_dps <= 0.
    // Matches the gateway "mid" preset.
    static constexpr int DEFAULT_SPEED_DPS = 120;
    static constexpr uint32_t MOTION_PER_WRITE_TIME_MS = 30;
    static_assert(
        MOTION_TICK_MS == kStackChanExpressionSampleIntervalMs,
        "expression validation must use the driver sample interval");
    static_assert(
        MAX_SPEED_DPS == kStackChanExpressionMaxSpeedDps,
        "expression validation must use the servo speed limit");
    static constexpr uint32_t MOTION_POLL_INTERVAL_MS = 50;
    static constexpr uint32_t AUTO_TORQUE_RELEASE_MIN_MS = 500;
    static constexpr uint32_t AUTO_TORQUE_RELEASE_MAX_MS = 600000;
    static constexpr int kMaxReengageRetries = 3;
    static constexpr int kMaxManualReengageRetries = 3;
#ifdef CONFIG_STACKCHAN_AUTO_TORQUE_RELEASE_MS
    static constexpr uint32_t AUTO_TORQUE_RELEASE_DEFAULT_MS =
        CONFIG_STACKCHAN_AUTO_TORQUE_RELEASE_MS;
#else
    static constexpr uint32_t AUTO_TORQUE_RELEASE_DEFAULT_MS = 5000;
#endif
    std::atomic<uint32_t> auto_release_timeout_ms_{
        AUTO_TORQUE_RELEASE_DEFAULT_MS};

    // Issue #80 / #98: pitch is guarded by two complementary tiers.
    //
    // Tier 1 — Hard clamp [SAFE_PITCH_MIN, SAFE_PITCH_MAX]:
    //   The absolute mechanical safety net. Its only job is to prevent
    //   physical damage to the servo / gear / chassis. Values are silently
    //   clamped to this range at every servo-write boundary and an
    //   ESP_LOGW is emitted when clamping occurs.
    //   - Lower bound 0°: the mechanical end-stop on the M5Stack CoreS3 +
    //     SCS0009 hardware sits very close to pitch=-1° (validated on a
    //     real unit, PR #81). Driving below 0° presses the servo gear into
    //     the physical stopper and produces an audible click.
    //   - Upper bound (SAFE_PITCH_MAX): chosen 1° inside the validated
    //     mechanical upper limit. The M5Stack-documented servo features
    //     advertise "90-degree movement on the vertical axis", so the
    //     mechanical upper end-stop is expected near 90°; the precise
    //     value is established by real-device sweep (Issue #98 validation).
    //
    // Tier 2 — Recommended operating range [RECOMMENDED_PITCH_MIN,
    //          RECOMMENDED_PITCH_MAX]:
    //   The M5Stack-documented sweet spot for long-term servo reliability
    //   (https://docs.m5stack.com/en/StackChan, "Motion Angle Notice":
    //   "The movement angle of the StackChan Y-axis servo (vertical
    //   direction) is recommended to be controlled within 5 ~ 85°.
    //   Operating at extreme angles may cause servo stall and permanent
    //   damage.").
    //   Values inside the hard clamp but outside this range are accepted
    //   (they are not hardware-damaging on a single call), and an
    //   ESP_LOGI is emitted so callers / agents can notice the deviation
    //   without blocking the motion.
    //
    // Defense-in-depth: the hard clamp is enforced at every servo-write
    // boundary —
    //   1. PitchDegToPos() clamps its input (covers motion-task
    //      interpolation and any future caller that bypasses the MCP
    //      layer).
    //   2. The start-up restore from ReadPos clamps the recovered angle
    //      so a device booting with the head physically pushed past the
    //      safe range does not carry that out-of-range starting angle
    //      into motion interpolation.
    //   3. The set_head_angles MCP handler additionally clamps the
    //      request target so the original out-of-range value is logged.
    static constexpr int SAFE_PITCH_MIN = 0;
    static constexpr int SAFE_PITCH_MAX = 88;  // Issue #98: validated on real hardware
                                                // (M5Stack CoreS3 + SCS0009 ×2). On-device
                                                // sweep observed clean motion at pitch=85
                                                // and pitch=88 reached without end-stop, but
                                                // pitch=89 exhibited an audible sub-stall
                                                // ("ji-ji-" gear strain sound). Mirrors PR #81
                                                // lower-bound rationale: stay 1° inside the
                                                // observed servo-strain boundary.
    static constexpr int RECOMMENDED_PITCH_MIN = 5;   // M5Stack official docs
    static constexpr int RECOMMENDED_PITCH_MAX = 85;  // M5Stack official docs

    // Issue #115: boot-time initialization target. Fall-safe neutral pose
    // well clear of both mechanical end-stops, in the centre of the
    // M5Stack-recommended 5..85° pitch range. Design follows the
    // goHome() pattern in m5stack/StackChan
    // (apps/app_setup/workers/servo.cpp:144) and the 1-second
    // positioning timing established in mongonta0716/stackchan-arduino
    // attachServos().
    //
    // Speed policy history (#121 Problem 2 -> #141 follow-ups):
    // - #121 Problem 2 originally raised BOOT_INIT_MOVE_MS from 1000 ms
    //   (the historical default that produced a startling "ブルンっ" boot
    //   motion) to 4000 ms (~11°/s on a 45° climb).
    // - Real-device verification then showed even 11°/s reads as
    //   perceptibly fast for the first boot-time servo motion, so the
    //   Phase 0 climb is pinned to an angular-speed cap of
    //   BOOT_INIT_TARGET_DEG_PER_SEC=15 deg/s. The WriteHeadAngles call
    //   sizes its duration from the actual yaw / pitch deltas so this
    //   cap holds on every axis. On the PMIC OFF/ON path Phase 0 stays
    //   a no-op of effect (the #138 safe-fallback seed makes start_deg
    //   == target_deg), so the BOOT_INIT_MOVE_MS budget simply elapses
    //   without WritePos movement.
    // The 100 ms post-settle vTaskDelay in InitializeServo() is
    // unchanged. The separate "unintended downward drop on power-on"
    // (#121 Problem 1) is addressed by the snap-suppress hold in
    // InitializeServo() Phase 1a (PR #137) and the ReadPos retry +
    // safe-fallback seed in this file (#138).
    //
    // Issue #138: promoted from local block scope inside
    // InitializeServo() to class-level static constexpr so the
    // safe-fallback branch in Phase 2 can seed
    // pitch_motion_.current_deg with BOOT_INIT_PITCH_DEG when the
    // pre-init ReadPos retries all fail. Without that seed, the
    // boot-init `WriteHeadAngles(0, 45, 4000)` interpolation would
    // start from the struct-default `current_deg=0` (== pos=620 at
    // deg=0, the lower mechanical end-stop) and walk WritePos calls
    // upward through end-stop-adjacent positions before reaching the
    // target, risking servo bus degradation if the SCS0009 wakes up
    // mid-sequence.
    static constexpr int BOOT_INIT_YAW_DEG = 0;
    static constexpr int BOOT_INIT_PITCH_DEG = 45;
    // BOOT_INIT_MOVE_MS=3000: minimum duration of the Phase 0 climb.
    // Used as a floor so the boot-init `WriteHeadAngles(0, 45, X)`
    // always elapses at least this long — required on the PMIC OFF/ON
    // path where the #138 safe-fallback seed makes Phase 0 a no-op of
    // effect, and the BOOT_INIT_MOVE_MS budget instead serves to span
    // the SCS0009 wake-up latency window so the post-init ReadPos
    // (Phase 0') lands well past it. The actual Phase 0 duration is
    // computed at call time from the current_deg → BOOT_INIT_*
    // deltas at BOOT_INIT_TARGET_DEG_PER_SEC=15 deg/s, then floored at this
    // constant; e.g. on the ESP32-only reset path with a yaw-90° prior
    // set-point, Phase 0 needs 6000 ms to honour the speed cap while a
    // yaw-0 prior is rounded up to this 3000 ms floor. This is the
    // no-stutter Smooth lower bound established under #121 Problem 2
    // (Issue #121 / PR #125 history: 1000 -> 4000 was a partial step
    // toward this; on-device feedback under #141 verification confirmed
    // 15 deg/s is the speed at which the ServoTask MOTION_TICK_MS=20 ms
    // interpolation stops being perceptible as individual position
    // jumps without sliding into a startling regime). Boot-time budget
    // is intentionally not optimised: operator safety and avoiding
    // mechanical stress take precedence over shaving seconds off the
    // initialization duration.
    //
    // On the PMIC OFF/ON path Phase 0 stays a no-op of effect
    // because the #138 safe-fallback seeds current_deg to
    // BOOT_INIT_PITCH_DEG, making start_deg == target_deg; the
    // BOOT_INIT_MOVE_MS budget then simply elapses without WritePos
    // movement.
    static constexpr uint32_t BOOT_INIT_MOVE_MS = 3000;
    // Single-source Phase 0 speed cap. 15 deg/s is the no-stutter Smooth
    // lower bound (#121 Problem 2 + #141 verification).
    static constexpr int BOOT_INIT_TARGET_DEG_PER_SEC = 15;

    static int YawDegToPos(int deg) {
        int pos = 460 + deg * 16 / 5;
        if (pos < 0) pos = 0;
        if (pos > 1000) pos = 1000;
        return pos;
    }

    static int PitchDegToPos(int deg) {
        // Issue #80: defense-in-depth — clamp at the servo-write boundary so
        // motion-task interpolation and any other future caller cannot
        // bypass the input-layer clamp.
        if (deg < SAFE_PITCH_MIN) deg = SAFE_PITCH_MIN;
        if (deg > SAFE_PITCH_MAX) deg = SAFE_PITCH_MAX;
        int pos = 620 + deg * 16 / 5;
        if (pos < 0) pos = 0;
        if (pos > 1000) pos = 1000;
        return pos;
    }

    static uint16_t clamp_u16(uint32_t v) {
        if (v > std::numeric_limits<uint16_t>::max()) {
            return std::numeric_limits<uint16_t>::max();
        }
        return static_cast<uint16_t>(v);
    }

    // Map the StartMove duration contract to spring options that approximate
    // the requested timing. This is the stackchan-mcp side of
    // m5stack/StackChan's map_speed_to_spring_options(speed): shorter
    // duration -> higher stiffness/damping, longer duration -> lower
    // stiffness/damping, with critical damping for no overshoot.
    static smooth_ui_toolkit::SpringOptions_t MapDurationToSpringOptions(
        uint32_t duration_ms) {
        if (duration_ms == 0) {
            duration_ms = 1;
        }

        float speed_f = 500.0f *
            (static_cast<float>(MOTION_DEFAULT_DURATION_MS) /
             static_cast<float>(duration_ms));
        if (speed_f < 1.0f) speed_f = 1.0f;
        if (speed_f > 1000.0f) speed_f = 1000.0f;
        int speed = static_cast<int>(speed_f);

        constexpr float kMin = 10.0f;
        constexpr float kMax = 650.0f;
        constexpr float kMass = 1.0f;
        float normalized_speed = static_cast<float>(speed) / 1000.0f;
        float stiffness =
            kMin + (normalized_speed * normalized_speed) * (kMax - kMin);
        float damping = 2.0f * std::sqrt(kMass * stiffness);

        smooth_ui_toolkit::SpringOptions_t options;
        options.stiffness = stiffness;
        options.damping = damping;
        options.mass = kMass;
        options.velocity = 0.0f;
        options.restDelta = speed > 800 ? 0.5f : 0.1f;
        options.restSpeed = speed > 800 ? 0.5f : 0.1f;
        options.duration = 0.0f;
        options.bounce = 0.0f;
        options.visualDuration = 0.0f;
        return options;
    }

    enum class ReleaseReason : uint8_t {
        kManual = 0,
        kAutoIdle,
        kReengagement,
    };

    static const char* ReleaseReasonName(ReleaseReason reason) {
        switch (reason) {
            case ReleaseReason::kManual:
                return "manual";
            case ReleaseReason::kAutoIdle:
                return "auto_idle";
            case ReleaseReason::kReengagement:
                return "reengagement";
        }
        return "unknown";
    }

    // Caller must hold scs_bus_mutex_.
    void PublishTorqueState() {
        TorqueState state;
        if (yaw_torque_enabled_ && pitch_torque_enabled_) {
            state = TorqueState::kEngaged;
        } else if (!yaw_torque_enabled_ && !pitch_torque_enabled_) {
            state = TorqueState::kReleased;
        } else {
            state = TorqueState::kPartial;
        }
        TorqueState old_state =
            torque_state_.load(std::memory_order_acquire);
        if (state == TorqueState::kEngaged &&
            old_state != TorqueState::kEngaged) {
            // Reset the ServoTask-owned idle window even when OFF->ON
            // happens between ServoTask ticks.
            idle_timer_reset_pending_.store(true,
                                            std::memory_order_release);
        }
        torque_state_.store(state, std::memory_order_release);
    }

    // Marks a fully-OFF transition while the bus write is still pending.
    // Returns this call's release epoch so auto-idle rollback can detect
    // another release publisher that interleaved after it.
    uint32_t MarkReleasing() {
        uint32_t epoch =
            torque_release_epoch_.fetch_add(1, std::memory_order_acq_rel) +
            1;
        torque_state_.store(TorqueState::kReleasing,
                            std::memory_order_release);
        return epoch;
    }

    // Block until torque_state_ leaves kReleasing or the elapsed-time
    // budget expires. Caller must NOT hold motion_mutex_ or scs_bus_mutex_.
    // Uses esp_timer_get_time() so the budget is honored at real time
    // regardless of CONFIG_FREERTOS_HZ.
    //
    // Returns true if the state is no longer kReleasing (proceed safely),
    // false if the wait budget was exhausted while still kReleasing
    // (caller decides how to handle: either skip with ESP_LOGW or defer to
    // its own bounded retry).
    bool WaitForKReleasingToClear() {
        constexpr uint32_t kMaxKReleasingWaitMs = 200;
        constexpr uint32_t kKReleasingPollIntervalMs = 5;
        const TickType_t kDelayTicks =
            std::max<TickType_t>(1,
                                 pdMS_TO_TICKS(kKReleasingPollIntervalMs));
        const uint32_t start_us =
            static_cast<uint32_t>(esp_timer_get_time());
        auto state = torque_state_.load(std::memory_order_acquire);
        while (state == TorqueState::kReleasing) {
            const uint32_t elapsed_ms =
                (static_cast<uint32_t>(esp_timer_get_time()) - start_us) /
                1000;
            if (elapsed_ms >= kMaxKReleasingWaitMs) {
                return false;
            }
            vTaskDelay(kDelayTicks);
            state = torque_state_.load(std::memory_order_acquire);
        }
        return true;
    }

    struct ServoTorqueResult {
        // -1 means "no bus frame was issued for this axis". In every
        // short-circuit path (idempotent_short_circuit or wait_exhausted)
        // the function returns before any EnableTorque() call, so both
        // bus-return fields keep this -1 default (Issue #171).
        int yaw_bus_return = -1;
        int pitch_bus_return = -1;
        bool yaw_ok = false;
        bool pitch_ok = false;
        // Issue #171: the old single `short_circuited` flag was overloaded
        // (set both for idempotent no-ops AND for wait-budget exhaustion),
        // so callers could not distinguish degraded-bus wait-exhaustion from
        // a legitimate no-op success. These two flags are orthogonal and
        // mutually exclusive: at most one is ever true.
        //   * idempotent_short_circuit: returned without a bus frame because
        //     the per-axis state already matched the request (success no-op).
        //   * wait_exhausted: returned without a bus frame because
        //     WaitForKReleasingToClear() hit its budget while still
        //     kReleasing (failure: the requested transition did not happen).
        bool idempotent_short_circuit = false;
        bool wait_exhausted = false;
    };

    class MotionDriver {
    public:
        virtual ~MotionDriver() = default;

        // Non-blocking. Both axes are dispatched within a single call.
        // WriteHeadAngles holds motion_mutex_ while calling this method; Tick()
        // and getters take the mutex internally for their own state access.
        virtual void StartMove(float yaw_deg, float pitch_deg,
                               uint32_t duration_ms,
                               bool prefer_linear = false) = 0;

        // Starts one continuous cubic Bezier motion. The caller holds
        // motion_mutex_, as for StartMove(). Drivers that cannot execute the
        // authored curve reject it instead of degrading it into rigid moves.
        virtual bool StartCurve(const StackChanExpressionStep&) {
            return false;
        }
        virtual bool SupportsCurve() const { return false; }
        virtual bool ConsumeCurveFailure() { return false; }

        // Last-known committed angle for each axis.
        virtual float GetYawDeg() const = 0;
        virtual float GetPitchDeg() const = 0;

        // True iff at least one axis is currently in motion.
        virtual bool IsMoving() const = 0;

        // Called from ServoTask body at a driver-dependent cadence.
        virtual void Tick() = 0;

        // Optional hooks for drivers that need setup or shutdown.
        virtual bool Initialize() { return true; }
        virtual void Shutdown() {}

        // Invalidate the freshness token for one axis. Used by board-
        // level code that mutates AxisMotion fields directly outside
        // StartMove (currently InitializeServo's Phase 0' post-init
        // ReadPos re-sync, and the set_servo_torque MCP tool's
        // disable path). Caller must hold motion_mutex_.
        //
        // HostInterpolationMotionDriver: bumps the per-axis
        // request_token, defeating any post-bus freshness check from a
        // Tick() snapshot taken before the external mutation.
        //
        // ServoDelegatedMotionDriver: bumps the per-axis request_token
        // AND clears the corresponding AxisServo's per-axis private
        // cancellation state (pending_dispatch_, dispatch_failures_,
        // readmove_failures_), atomically with the caller's motion_mutex_
        // hold.
        //
        // Drivers without a token-based freshness guard treat this as
        // a no-op (default implementation). Argument is SERVO_YAW_ID
        // or SERVO_PITCH_ID; unknown values are ignored.
        virtual void InvalidateAxisToken(int /*axis_id*/) {}
    };

    class HostInterpolationMotionDriver final : public MotionDriver {
    public:
        HostInterpolationMotionDriver(ScsBus& scs_bus,
                                      SemaphoreHandle_t& scs_bus_mutex,
                                      SemaphoreHandle_t& motion_mutex,
                                      AxisMotion& yaw_motion,
                                      AxisMotion& pitch_motion)
            : scs_bus_(scs_bus),
              scs_bus_mutex_(scs_bus_mutex),
              motion_mutex_(motion_mutex),
              yaw_motion_(yaw_motion),
              pitch_motion_(pitch_motion),
              next_request_token_(0) {
            yaw_anim_.teleport(static_cast<float>(yaw_motion_.current_deg));
            pitch_anim_.teleport(static_cast<float>(pitch_motion_.current_deg));
        }

        void StartMove(float yaw_deg, float pitch_deg,
                       uint32_t duration_ms,
                       bool prefer_linear) override {
            uint32_t now_ms = static_cast<uint32_t>(esp_timer_get_time() / 1000);
            int yaw = static_cast<int>(yaw_deg);
            int pitch = static_cast<int>(pitch_deg);

            yaw_motion_.request_token = ++next_request_token_;
            yaw_motion_.target_deg = yaw;
            yaw_motion_.start_deg = yaw_motion_.current_deg;
            yaw_motion_.move_start_ms = now_ms;
            yaw_motion_.move_duration_ms = duration_ms;
            yaw_motion_.moving = (yaw_motion_.target_deg != yaw_motion_.current_deg);
            yaw_linear_mode_ = prefer_linear;

            pitch_motion_.request_token = ++next_request_token_;
            pitch_motion_.target_deg = pitch;
            pitch_motion_.start_deg = pitch_motion_.current_deg;
            pitch_motion_.move_start_ms = now_ms;
            pitch_motion_.move_duration_ms = duration_ms;
            pitch_motion_.moving = (pitch_motion_.target_deg != pitch_motion_.current_deg);
            pitch_linear_mode_ = prefer_linear;
            yaw_curve_mode_ = false;
            pitch_curve_mode_ = false;
            if (prefer_linear) {
                yaw_anim_.teleport(static_cast<float>(yaw_motion_.current_deg));
                yaw_snap_on_rest_ = false;
                pitch_anim_.teleport(static_cast<float>(pitch_motion_.current_deg));
                pitch_snap_on_rest_ = false;
                return;
            }

            smooth_ui_toolkit::SpringOptions_t spring_options =
                MapDurationToSpringOptions(duration_ms);
            StartAxisSpring(yaw_anim_, yaw_snap_on_rest_,
                            yaw_motion_.current_deg, yaw,
                            yaw_motion_.moving, spring_options);
            StartAxisSpring(pitch_anim_, pitch_snap_on_rest_,
                            pitch_motion_.current_deg, pitch,
                            pitch_motion_.moving, spring_options);
        }

        bool StartCurve(const StackChanExpressionStep& step) override {
            if (yaw_motion_.current_deg != step.points[0].yaw ||
                pitch_motion_.current_deg != step.points[0].pitch) {
                return false;
            }
            uint32_t now_ms = static_cast<uint32_t>(esp_timer_get_time() / 1000);
            curve_step_ = step;
            curve_failure_ = false;
            curve_elapsed_ms_ = 0;
            last_wake_tick_ = xTaskGetTickCount();
            auto start_axis = [this, now_ms, &step](
                                  AxisMotion& motion,
                                  bool& curve_mode,
                                  bool& linear_mode,
                                  bool yaw) {
                motion.request_token = ++next_request_token_;
                motion.start_deg = yaw
                    ? step.points[0].yaw : step.points[0].pitch;
                motion.target_deg = yaw
                    ? step.points[3].yaw : step.points[3].pitch;
                motion.move_start_ms = now_ms;
                motion.move_duration_ms =
                    static_cast<uint32_t>(step.duration_ms);
                motion.moving = false;
                for (size_t index = 1; index < step.points.size(); ++index) {
                    const int point = yaw
                        ? step.points[index].yaw : step.points[index].pitch;
                    motion.moving = motion.moving || point != motion.start_deg;
                }
                curve_mode = true;
                linear_mode = false;
            };
            start_axis(
                yaw_motion_, yaw_curve_mode_, yaw_linear_mode_, true);
            start_axis(
                pitch_motion_, pitch_curve_mode_, pitch_linear_mode_, false);
            return true;
        }

        bool SupportsCurve() const override { return true; }

        bool ConsumeCurveFailure() override {
            xSemaphoreTake(motion_mutex_, portMAX_DELAY);
            const bool failed = curve_failure_;
            curve_failure_ = false;
            xSemaphoreGive(motion_mutex_);
            return failed;
        }

        float GetYawDeg() const override {
            xSemaphoreTake(motion_mutex_, portMAX_DELAY);
            int yaw = yaw_motion_.current_deg;
            xSemaphoreGive(motion_mutex_);
            return static_cast<float>(yaw);
        }

        float GetPitchDeg() const override {
            xSemaphoreTake(motion_mutex_, portMAX_DELAY);
            int pitch = pitch_motion_.current_deg;
            xSemaphoreGive(motion_mutex_);
            return static_cast<float>(pitch);
        }

        bool IsMoving() const override {
            xSemaphoreTake(motion_mutex_, portMAX_DELAY);
            bool moving = yaw_motion_.moving || pitch_motion_.moving;
            xSemaphoreGive(motion_mutex_);
            return moving;
        }

        void Tick() override {
            constexpr TickType_t kInterFrameGap = pdMS_TO_TICKS(10);
            uint32_t tick_interval_ms = MOTION_TICK_MS;
            xSemaphoreTake(motion_mutex_, portMAX_DELAY);
            if ((yaw_curve_mode_ || pitch_curve_mode_) &&
                curve_elapsed_ms_ <
                    static_cast<uint32_t>(curve_step_.duration_ms)) {
                tick_interval_ms =
                    NextStackChanExpressionSampleElapsedMs(
                        curve_elapsed_ms_,
                        static_cast<uint32_t>(curve_step_.duration_ms)) -
                    curve_elapsed_ms_;
            }
            xSemaphoreGive(motion_mutex_);
            const TickType_t tick_interval = std::max<TickType_t>(
                1,
                pdMS_TO_TICKS(
                    tick_interval_ms + portTICK_PERIOD_MS - 1));
            const TickType_t tick_now = xTaskGetTickCount();
            if (last_wake_tick_ == 0) {
                last_wake_tick_ = tick_now;
            }
            if (xTaskDelayUntil(&last_wake_tick_, tick_interval) == pdFALSE) {
                // Re-anchor missed slots instead of issuing catch-up frames.
                last_wake_tick_ = xTaskGetTickCount();
            }
            AxisMotion yaw_local;
            AxisMotion pitch_local;
            int new_yaw_current;
            bool new_yaw_moving;
            int new_pitch_current;
            bool new_pitch_moving;
            bool yaw_linear_mode;
            bool pitch_linear_mode;
            bool yaw_curve_mode;
            bool pitch_curve_mode;
            uint32_t current_curve_elapsed_ms;
            uint32_t scheduled_curve_elapsed_ms;
            uint64_t now_us = static_cast<uint64_t>(esp_timer_get_time());
            float dt_s;
            // Spring mode follows real elapsed time so bus ACK latency or
            // mutex contention does not stretch animation time indefinitely.
            // Clamp deep preemption to avoid a single large lurch.
            if (last_tick_us_ == 0) {
                dt_s = static_cast<float>(MOTION_TICK_MS) / 1000.0f;
            } else {
                dt_s = static_cast<float>(now_us - last_tick_us_) / 1000000.0f;
                if (dt_s > 0.1f) {
                    dt_s = 0.1f;
                }
            }
            last_tick_us_ = now_us;
            uint32_t now_ms = static_cast<uint32_t>(now_us / 1000);

            xSemaphoreTake(motion_mutex_, portMAX_DELAY);
            yaw_local = yaw_motion_;
            pitch_local = pitch_motion_;
            yaw_linear_mode = yaw_linear_mode_;
            pitch_linear_mode = pitch_linear_mode_;
            yaw_curve_mode = yaw_curve_mode_;
            pitch_curve_mode = pitch_curve_mode_;
            current_curve_elapsed_ms = curve_elapsed_ms_;
            scheduled_curve_elapsed_ms = current_curve_elapsed_ms;
            if (yaw_curve_mode || pitch_curve_mode) {
                scheduled_curve_elapsed_ms =
                    NextStackChanExpressionSampleElapsedMs(
                        current_curve_elapsed_ms,
                        static_cast<uint32_t>(curve_step_.duration_ms));
            }
            if (!yaw_local.moving && !pitch_local.moving) {
                xSemaphoreGive(motion_mutex_);
                return;
            }
            new_yaw_current = yaw_local.current_deg;
            new_yaw_moving = yaw_local.moving;
            if (yaw_curve_mode) {
                AdvanceAxisCurve(
                    yaw_local, curve_step_, true,
                    scheduled_curve_elapsed_ms,
                    new_yaw_current, new_yaw_moving);
            } else if (yaw_linear_mode) {
                AdvanceAxisLinear(yaw_local, now_ms,
                                  new_yaw_current, new_yaw_moving);
            } else {
                AdvanceAxisSpring(yaw_local, yaw_anim_, yaw_snap_on_rest_,
                                  dt_s, new_yaw_current, new_yaw_moving);
            }
            new_pitch_current = pitch_local.current_deg;
            new_pitch_moving = pitch_local.moving;
            if (pitch_curve_mode) {
                AdvanceAxisCurve(
                    pitch_local, curve_step_, false,
                    scheduled_curve_elapsed_ms,
                    new_pitch_current, new_pitch_moving);
            } else if (pitch_linear_mode) {
                AdvanceAxisLinear(pitch_local, now_ms,
                                  new_pitch_current, new_pitch_moving);
            } else {
                AdvanceAxisSpring(pitch_local, pitch_anim_, pitch_snap_on_rest_,
                                  dt_s, new_pitch_current, new_pitch_moving);
            }
            xSemaphoreGive(motion_mutex_);

            const int yaw_position = yaw_curve_mode
                ? EvaluateCurvePosition(
                    curve_step_, true, scheduled_curve_elapsed_ms)
                : YawDegToPos(new_yaw_current);
            const int pitch_position = pitch_curve_mode
                ? EvaluateCurvePosition(
                    curve_step_, false, scheduled_curve_elapsed_ms)
                : PitchDegToPos(new_pitch_current);
            const bool unsafe_curve_command =
                (yaw_curve_mode && !IsCurvePositionVelocitySafe(
                    EvaluateCurvePosition(
                        curve_step_, true, current_curve_elapsed_ms),
                    yaw_position)) ||
                (pitch_curve_mode && !IsCurvePositionVelocitySafe(
                    EvaluateCurvePosition(
                        curve_step_, false, current_curve_elapsed_ms),
                    pitch_position));
            if (unsafe_curve_command) {
                ESP_LOGE(TAG, "Expression curve exceeded velocity limit");
                FailCurve(
                    yaw_local.request_token, pitch_local.request_token);
                return;
            }

            bool curve_write_failed = false;
            xSemaphoreTake(scs_bus_mutex_, portMAX_DELAY);
            xSemaphoreTake(motion_mutex_, portMAX_DELAY);
            const bool request_live =
                yaw_motion_.request_token == yaw_local.request_token &&
                pitch_motion_.request_token == pitch_local.request_token;
            xSemaphoreGive(motion_mutex_);
            if (!request_live) {
                xSemaphoreGive(scs_bus_mutex_);
                return;
            }
            if (yaw_curve_mode || pitch_curve_mode) {
                const int result = scs_bus_.SyncWritePos(
                    SERVO_YAW_ID,
                    static_cast<uint16_t>(yaw_position),
                    SERVO_PITCH_ID,
                    static_cast<uint16_t>(pitch_position),
                    MOTION_PER_WRITE_TIME_MS,
                    0);
                curve_write_failed = !ServoWritePosOk(result);
                if (curve_write_failed) {
                    ESP_LOGW(
                        TAG,
                        "Expression synchronized WritePos failed: r=%d",
                        result);
                }
            } else {
                if (yaw_local.moving) {
                    int yaw_pos = YawDegToPos(new_yaw_current);
                    int r = scs_bus_.WritePos(
                        SERVO_YAW_ID, yaw_pos,
                        MOTION_PER_WRITE_TIME_MS, 0);
                    if (!ServoWritePosOk(r)) {
                        ESP_LOGW(
                            TAG,
                            "Motion yaw WritePos failed: "
                            "r=%d (deg=%d, pos=%d)",
                            r, new_yaw_current, yaw_pos);
                    }
                }
                vTaskDelay(kInterFrameGap);
                if (pitch_local.moving) {
                    int pitch_pos = PitchDegToPos(new_pitch_current);
                    int r = scs_bus_.WritePos(
                        SERVO_PITCH_ID, pitch_pos,
                        MOTION_PER_WRITE_TIME_MS, 0);
                    if (!ServoWritePosOk(r)) {
                        ESP_LOGW(
                            TAG,
                            "Motion pitch WritePos failed: "
                            "r=%d (deg=%d, pos=%d)",
                            r, new_pitch_current, pitch_pos);
                    }
                }
            }
            xSemaphoreGive(scs_bus_mutex_);

            if (curve_write_failed) {
                FailCurve(
                    yaw_local.request_token, pitch_local.request_token);
                return;
            }

            xSemaphoreTake(motion_mutex_, portMAX_DELAY);
            if (yaw_motion_.request_token == yaw_local.request_token) {
                yaw_motion_.current_deg = new_yaw_current;
            }
            if (!new_yaw_moving && yaw_motion_.target_deg == yaw_local.target_deg
                && yaw_motion_.request_token == yaw_local.request_token) {
                yaw_motion_.moving = false;
            }
            if (pitch_motion_.request_token == pitch_local.request_token) {
                pitch_motion_.current_deg = new_pitch_current;
            }
            if (!new_pitch_moving && pitch_motion_.target_deg == pitch_local.target_deg
                && pitch_motion_.request_token == pitch_local.request_token) {
                pitch_motion_.moving = false;
            }
            if (yaw_curve_mode && pitch_curve_mode &&
                yaw_motion_.request_token == yaw_local.request_token &&
                pitch_motion_.request_token ==
                    pitch_local.request_token) {
                curve_elapsed_ms_ = scheduled_curve_elapsed_ms;
            }
            xSemaphoreGive(motion_mutex_);
        }

        // Bump the request token for the specified axis. Used by
        // InitializeServo's Phase 0' re-sync so a Tick() snapshot
        // taken before the re-sync no longer passes the post-bus
        // freshness guard (which would otherwise overwrite the just-
        // re-synced current_deg / moving state). Caller must hold
        // motion_mutex_; this method does not take it.
        void InvalidateAxisToken(int axis_id) override {
            if (axis_id == SERVO_YAW_ID) {
                yaw_motion_.request_token = ++next_request_token_;
            } else if (axis_id == SERVO_PITCH_ID) {
                pitch_motion_.request_token = ++next_request_token_;
            }
        }

    private:
        void FailCurve(uint64_t yaw_token, uint64_t pitch_token) {
            xSemaphoreTake(motion_mutex_, portMAX_DELAY);
            if (yaw_motion_.request_token == yaw_token &&
                pitch_motion_.request_token == pitch_token) {
                yaw_motion_.moving = false;
                pitch_motion_.moving = false;
                yaw_curve_mode_ = false;
                pitch_curve_mode_ = false;
                curve_failure_ = true;
            }
            xSemaphoreGive(motion_mutex_);
        }

        static int EvaluateCurvePosition(
                const StackChanExpressionStep& step,
                bool yaw,
                uint32_t elapsed_ms) {
            std::array<int, 4> positions;
            for (size_t index = 0; index < positions.size(); ++index) {
                positions[index] = yaw
                    ? YawDegToPos(step.points[index].yaw)
                    : PitchDegToPos(step.points[index].pitch);
            }
            return EvaluateStackChanExpressionCurve(
                positions,
                static_cast<uint32_t>(step.duration_ms),
                elapsed_ms);
        }

        static bool IsCurvePositionVelocitySafe(int previous, int next) {
            return IsStackChanExpressionVelocitySafe(
                previous * 5,
                next * 5,
                MOTION_PER_WRITE_TIME_MS,
                MAX_SPEED_DPS,
                16);
        }

        static void StartAxisSpring(
            smooth_ui_toolkit::AnimateValue& axis_anim,
            bool& snap_on_rest,
            int current_deg,
            int target_deg,
            bool moving,
            const smooth_ui_toolkit::SpringOptions_t& spring_options) {
            if (!moving) {
                axis_anim.teleport(static_cast<float>(current_deg));
                snap_on_rest = false;
                return;
            }

            axis_anim.springOptions() = spring_options;
            axis_anim.teleport(static_cast<float>(current_deg));
            axis_anim = static_cast<float>(target_deg);
            snap_on_rest = true;
        }

        static void AdvanceAxisSpring(
            const AxisMotion& axis_local,
            smooth_ui_toolkit::AnimateValue& axis_anim,
            bool& snap_on_rest,
            float dt_s,
            int& new_current_deg,
            bool& new_moving) {
            if (!axis_local.moving) {
                return;
            }

            axis_anim.updateWithDelta(dt_s);
            new_current_deg = static_cast<int>(axis_anim.directValue());
            if (axis_anim.done()) {
                new_moving = false;
                if (snap_on_rest) {
                    new_current_deg = static_cast<int>(axis_anim.end);
                    snap_on_rest = false;
                }
            }
        }

        static void AdvanceAxisLinear(
            const AxisMotion& axis_local,
            uint32_t now_ms,
            int& new_current_deg,
            bool& new_moving) {
            if (!axis_local.moving) {
                return;
            }

            // Linear mode intentionally stays wall-clock based, matching the
            // pre-spring interpolation path used for boot-init slow climbs;
            // spring mode uses the real elapsed Tick delta instead.
            uint32_t elapsed = now_ms - axis_local.move_start_ms;
            if (axis_local.move_duration_ms == 0 ||
                elapsed >= axis_local.move_duration_ms) {
                new_current_deg = axis_local.target_deg;
                new_moving = false;
            } else {
                int delta = axis_local.target_deg - axis_local.start_deg;
                new_current_deg = axis_local.start_deg +
                    static_cast<int>(
                        static_cast<int64_t>(delta) * elapsed /
                        axis_local.move_duration_ms);
            }
        }

        static void AdvanceAxisCurve(
            const AxisMotion& axis_local,
            const StackChanExpressionStep& step,
            bool yaw,
            uint32_t elapsed_ms,
            int& new_current_deg,
            bool& new_moving) {
            if (!axis_local.moving) {
                return;
            }
            new_current_deg = EvaluateStackChanExpressionAxis(
                step, yaw, elapsed_ms);
            if (elapsed_ms >= axis_local.move_duration_ms) {
                new_moving = false;
            }
        }

        ScsBus& scs_bus_;
        SemaphoreHandle_t& scs_bus_mutex_;
        SemaphoreHandle_t& motion_mutex_;
        AxisMotion& yaw_motion_;
        AxisMotion& pitch_motion_;
        // Monotonically increasing request id. Each StartMove increments
        // this and writes the new value into yaw_motion_.request_token
        // and pitch_motion_.request_token. Tick() snapshots both fields
        // with the rest of AxisMotion and uses request_token equality
        // (rather than move_start_ms, which only has ms resolution) to
        // detect whether a snapshot is still the live request.
        // InvalidateAxisToken() also bumps this counter for board-level
        // direct AxisMotion resets that do not go through StartMove
        // (currently InitializeServo's Phase 0' post-init ReadPos
        // re-sync and the set_servo_torque disable path); without that
        // bump, a Tick() snapshot taken before such a reset would pass
        // the post-bus freshness guard and overwrite the just-reset
        // state. motion_mutex_ guards this counter. The pre-bus
        // stale-WritePos race, where an external reset lands after the
        // snapshot but before the bus frame, is tracked separately
        // under #161.
        uint64_t next_request_token_ = 0;
        smooth_ui_toolkit::AnimateValue yaw_anim_;
        smooth_ui_toolkit::AnimateValue pitch_anim_;
        bool yaw_snap_on_rest_ = false;
        bool pitch_snap_on_rest_ = false;
        bool yaw_linear_mode_ = false;
        bool pitch_linear_mode_ = false;
        bool yaw_curve_mode_ = false;
        bool pitch_curve_mode_ = false;
        StackChanExpressionStep curve_step_;
        uint32_t curve_elapsed_ms_ = 0;
        bool curve_failure_ = false;
        TickType_t last_wake_tick_ = 0;
        uint64_t last_tick_us_ = 0;
    };

    class ServoDelegatedMotionDriver final : public MotionDriver {
    public:
        ServoDelegatedMotionDriver(ScsBus& scs_bus,
                                   SemaphoreHandle_t& scs_bus_mutex,
                                   SemaphoreHandle_t& motion_mutex,
                                   AxisMotion& yaw_motion,
                                   AxisMotion& pitch_motion)
            : motion_mutex_(motion_mutex),
              yaw_motion_(yaw_motion),
              pitch_motion_(pitch_motion),
              next_request_token_(0),
              yaw_axis_(SERVO_YAW_ID, YawDegToPos, "yaw", yaw_motion,
                        scs_bus, scs_bus_mutex, motion_mutex,
                        next_request_token_,
                        /* post_dispatch_quiet_gap_ms = */ 10),
              pitch_axis_(SERVO_PITCH_ID, PitchDegToPos, "pitch",
                          pitch_motion, scs_bus, scs_bus_mutex,
                          motion_mutex, next_request_token_,
                          /* post_dispatch_quiet_gap_ms = */ 0) {}

        // StartMove only mutates AxisMotion state (under the caller's
        // motion_mutex_ per the MotionDriver::StartMove contract) and marks
        // each non-noop axis as having a pending dispatch. The actual WritePos
        // is performed by Tick() on the servo_motion task, so callers running
        // on timer tasks (e.g. TouchPollCb -> StartServoWobble) never block
        // on UART I/O. This mirrors HostInterpolationMotionDriver's
        // "StartMove writes state; Tick drives the bus" split and keeps
        // timer-task latency bounded.
        void StartMove(float yaw_deg, float pitch_deg,
                       uint32_t duration_ms,
                       bool prefer_linear) override {
            // The delegated path is already duration-bounded by the SCS0009
            // internal interpolation time argument; there is no host-side
            // profile to switch.
            (void)prefer_linear;
            uint16_t clamped = clamp_u16(duration_ms);
            if (duration_ms > clamped) {
                static bool duration_overflow_warned = false;
                if (!duration_overflow_warned) {
                    ESP_LOGW(TAG,
                             "Servo-delegated motion duration overflow: axis=yaw/pitch requested_ms=%u clamped_ms=%u",
                             (unsigned)duration_ms, (unsigned)clamped);
                    duration_overflow_warned = true;
                } else {
                    ESP_LOGD(TAG,
                             "Servo-delegated motion duration overflow: axis=yaw/pitch requested_ms=%u clamped_ms=%u",
                             (unsigned)duration_ms, (unsigned)clamped);
                }
            }

            // Per-axis no-op detection: when the axis is idle AND the request
            // matches the last-known current_deg AND the position is not
            // marked unknown, skip staging a dispatch. Issuing WritePos in
            // that case would start a delegated motion toward host-side
            // current_deg, which may diverge from the physical position
            // (e.g. after a boot ReadPos-failure path where current_deg was
            // seeded to BOOT_INIT_* via the safe fallback but the head sits
            // elsewhere). HostInterpolation path keeps its always-WritePos
            // behaviour for backward compatibility.
            //
            // position_unknown is the recovery signal from a prior ReadMove
            // force-clear: current_deg holds the requested target but
            // physical completion was never confirmed, so we MUST re-dispatch
            // (even when target == current_deg) to surface a persistent
            // failure rather than silently treat the axis as at-target.
            //
            // motion_mutex_ is held by the WriteHeadAngles caller per the
            // MotionDriver::StartMove contract (declared at the base class).
            // Taking it again here would deadlock the non-recursive FreeRTOS
            // semaphore — Stage() reads its AxisMotion directly.
            yaw_axis_.Stage(static_cast<int>(yaw_deg), clamped);
            pitch_axis_.Stage(static_cast<int>(pitch_deg), clamped);
        }

        float GetYawDeg() const override {
            xSemaphoreTake(motion_mutex_, portMAX_DELAY);
            int yaw = yaw_motion_.current_deg;
            xSemaphoreGive(motion_mutex_);
            return static_cast<float>(yaw);
        }

        float GetPitchDeg() const override {
            xSemaphoreTake(motion_mutex_, portMAX_DELAY);
            int pitch = pitch_motion_.current_deg;
            xSemaphoreGive(motion_mutex_);
            return static_cast<float>(pitch);
        }

        bool IsMoving() const override {
            xSemaphoreTake(motion_mutex_, portMAX_DELAY);
            bool moving = yaw_motion_.moving || pitch_motion_.moving;
            xSemaphoreGive(motion_mutex_);
            return moving;
        }

        void Tick() override {
            vTaskDelay(pdMS_TO_TICKS(MOTION_POLL_INTERVAL_MS));

            // Per-axis update. Post-bus-frame quiet period is held INSIDE
            // each axis's Dispatch() (WritePos) and PollReadMove()
            // (ReadMove) atomically with the bus frame itself (no release
            // / reacquire window where concurrent MCP callers could inject
            // bus frames). yaw is configured with 10 ms via
            // post_dispatch_quiet_gap_ms_ (the member name pre-dates the
            // post-ReadMove path but the value applies symmetrically to
            // both frame types); pitch is configured with 0 ms matching
            // the PR #146 empirical model.
            //
            // Inter-axis quiet period coverage:
            // - yaw Dispatch tick (WritePos): in-Dispatch 10 ms hold
            //   provides inter-axis spacing before pitch_axis_.Update().
            // - yaw PollReadMove tick (ReadMove): in-PollReadMove 10 ms
            //   hold provides the same inter-axis spacing — prevents the
            //   ReadMove -> WritePos 0 ms inter-frame sequence on the
            //   shared SCS bus that Phase 2's per-axis grain would
            //   otherwise expose (PR #146 had no such ordering because
            //   dispatch and poll Tick phases were mutually exclusive).
            // - yaw no-op tick: no yaw bus frame, no quiet period needed;
            //   pitch_axis_.Update() runs immediately.
            //
            // Two-axis simultaneous dispatch (the necessary side of the
            // PR #146 E2 / E4-cumulative hang trigger) remains
            // structurally eliminated: yaw and pitch each take
            // scs_bus_mutex_ in their own separate short-hold critical
            // section inside Update(), and pitch_axis_.Update() runs
            // only after yaw_axis_.Update() returns (i.e. after yaw's
            // scs_bus_mutex_ hold has been released).
            //
            // No inter-axis vTaskDelay at this wrapper level: every yaw
            // bus-frame-emitting branch already provides the 10 ms
            // wall-clock spacing inside its in-Method hold. The 10 ms
            // inter-frame budget also remains in the unchanged
            // HostInterpolationMotionDriver::Tick path.
            yaw_axis_.Update();
            pitch_axis_.Update();
        }

        // Caller holds motion_mutex_. Bumps next_request_token_ for the
        // specified axis (mirrors HostInterpolationMotionDriver's
        // implementation) AND clears the per-AxisServo private
        // cancellation state via OnExternalReset(), so that a Stage()
        // call that preceded the external mutation does not leak a stale
        // WritePos onto the bus from the next Update() tick.
        //
        // Used by InitializeServo's Phase 0' post-init ReadPos re-sync
        // and by the set_servo_torque MCP tool's disable path.
        //
        // Closing this cancellation boundary required a paired AxisMotion
        // (visible) + AxisServo (driver-private) atomic reset: bumping
        // the visible request_token alone (the default no-op fallback
        // this driver used to inherit) was insufficient because
        // pending_dispatch_ / dispatch_failures_ / readmove_failures_
        // survived a Phase 0' direct AxisMotion mutation and the next
        // Update() tick would re-issue a WritePos for the stale staged
        // target. Issue #160 tracks the design discussion and the
        // adversarial review that converged on this Option A design.
        //
        // Scope note: this closes the post-Update / next-tick race only.
        // A snapshot taken by Update() BEFORE the external reset still
        // carries a local `dispatch = true` boolean (Update()'s local
        // variable, not the member field) that survives this member-
        // state clear. The in-flight Dispatch() then passes the
        // request_token freshness gate at the pre-WritePos check (which
        // now sees a bumped token) and skips the WritePos itself, so
        // the bus is not touched with a stale frame — but the call
        // still acquires scs_bus_mutex_ once before returning. The
        // pre-bus stale-command race (Issue #161) is the remaining
        // cancellation-boundary layer and is intentionally NOT closed
        // by this PR.
        void InvalidateAxisToken(int axis_id) override {
            if (axis_id == SERVO_YAW_ID) {
                yaw_motion_.request_token = ++next_request_token_;
                yaw_axis_.OnExternalReset();
            } else if (axis_id == SERVO_PITCH_ID) {
                pitch_motion_.request_token = ++next_request_token_;
                pitch_axis_.OnExternalReset();
            }
        }

    private:
        static constexpr int kReadMoveFailureLimit = 5;
        static constexpr int kDispatchFailureLimit = 5;
        // Settle margin past move_duration_ms before treating a
        // stuck-high ReadMove (servo returns 1 forever after the
        // requested completion) as a failure. Without this bound,
        // a degraded servo / register path would keep moving=true
        // indefinitely; wobble would never advance and same-target
        // recovery would never run.
        static constexpr uint32_t kReadMoveStuckMarginMs = 1000;
        // Margin below move_duration_ms in which an early ReadMove==0
        // is allowed as a genuine completion (the SCS0009's internal
        // interpolation can finish slightly early). A ReadMove==0
        // arriving further before the commanded completion is
        // implausible and likely a stuck-low / false-zero status read
        // from a degraded register path; treat it as suspicious and
        // mark position_unknown so the next StartMove forces a fresh
        // dispatch instead of trusting the stale optimistic commit.
        static constexpr uint32_t kReadMoveEarlyMarginMs = 200;

        class AxisServo {
            // Lock-order audit for Update():
            // - Snapshot: motion_mutex_ only.
            // - Dispatch freshness gate: scs_bus_mutex_ -> motion_mutex_;
            //   motion_mutex_ is released before WritePos.
            // - Dispatch commit: motion_mutex_ only after scs_bus_mutex_ is
            //   released.
            // - ReadMove poll: scs_bus_mutex_ only, then motion_mutex_ only
            //   for commit. No path takes motion_mutex_ before scs_bus_mutex_.

        public:
            AxisServo(uint8_t servo_id, int (*deg_to_pos)(int),
                      const char* axis_name, AxisMotion& motion,
                      ScsBus& scs_bus, SemaphoreHandle_t& scs_bus_mutex,
                      SemaphoreHandle_t& motion_mutex,
                      uint64_t& next_request_token,
                      uint32_t post_dispatch_quiet_gap_ms)
                : servo_id_(servo_id),
                  deg_to_pos_(deg_to_pos),
                  axis_name_(axis_name),
                  motion_(motion),
                  scs_bus_(scs_bus),
                  scs_bus_mutex_(scs_bus_mutex),
                  motion_mutex_(motion_mutex),
                  next_request_token_(next_request_token),
                  post_dispatch_quiet_gap_ms_(post_dispatch_quiet_gap_ms) {}

            void Stage(int target_deg, uint16_t duration_ms) {
                // motion_mutex_ is held by the WriteHeadAngles caller per
                // the MotionDriver::StartMove contract. Taking it again here
                // would deadlock the non-recursive FreeRTOS semaphore.
                bool noop =
                    !motion_.moving && !motion_.position_unknown &&
                    target_deg == motion_.current_deg;
                if (noop) {
                    return;
                }

                uint32_t now_ms =
                    static_cast<uint32_t>(esp_timer_get_time() / 1000);
                motion_.start_deg = motion_.current_deg;
                motion_.target_deg = target_deg;
                motion_.move_start_ms = now_ms;
                // dispatch_start_ms stays 0 until FinishDispatch confirms
                // a WritePos ACK. ApplyReadMoveResult's stuck-high timeout
                // skips the check while dispatch_start_ms is 0, so retry
                // latency does not eat into the servo-internal duration
                // budget.
                motion_.dispatch_start_ms = 0;
                motion_.move_duration_ms = duration_ms;
                motion_.moving = true;
                motion_.request_token = ++next_request_token_;
                // NOTE: position_unknown is NOT cleared here. It is cleared
                // by FinishDispatch only after a successful WritePos ACK.
                // Clearing it on stage would let dispatch retry exhaustion
                // leave position_unknown=false despite no confirmed physical
                // motion; then the next same-target StartMove would no-op
                // skip on current_deg==target_deg and hide the bus failure.
                pending_dispatch_ = true;
                // Fresh request: reset the per-axis dispatch retry budget so
                // previous failures do not shorten this request's runway.
                dispatch_failures_ = 0;
            }

            // Returns true if this tick emitted any bus frame on the SCS
            // bus (WritePos via Dispatch() or ReadMove via PollReadMove()).
            // The wrapper Tick() does not use the bool to apply any
            // additional hold — both Dispatch() and PollReadMove() each
            // hold scs_bus_mutex_ atomically across their bus frame AND
            // the post-frame post_dispatch_quiet_gap_ms_ (yaw: 10 ms,
            // pitch: 0 ms), so the inter-axis quiet period is enforced
            // inside each axis's method without any release/reacquire
            // window. The return value is informational (kept for
            // diagnostic clarity and potential future use).
            bool Update() {
                AxisMotion snapshot;
                bool dispatch = false;
                xSemaphoreTake(motion_mutex_, portMAX_DELAY);
                snapshot = motion_;
                dispatch = pending_dispatch_;
                // Do NOT clear pending_dispatch here. FinishDispatch consumes
                // it only on success or retry exhaustion; transient WritePos
                // failures keep it true so the next tick retries the same
                // target instead of silently dropping the request.
                xSemaphoreGive(motion_mutex_);

                // With per-axis Update(), dispatch-vs-poll is chosen per
                // servo. One axis can spend this tick dispatching while the
                // other axis polls ReadMove after the wrapper's inter-axis
                // wall-clock gap.
                if (dispatch) {
                    return Dispatch(snapshot);
                }
                if (snapshot.moving) {
                    PollReadMove(snapshot);
                }
                return false;
            }

            // Caller must hold motion_mutex_. Clears the per-axis private
            // cancellation state so that a subsequent Update() tick observes
            // a clean slate after the board-level code directly mutates
            // AxisMotion outside the Stage() path (currently
            // InitializeServo's Phase 0' post-init ReadPos re-sync and the
            // set_servo_torque MCP tool's disable path).
            //
            // Does NOT take any semaphore (motion_mutex_ is already held by
            // the caller; double-take of the non-recursive FreeRTOS
            // semaphore would deadlock). Does NOT touch the SCS bus. Does
            // NOT touch motion_ (AxisMotion); the caller has already mutated
            // it before invoking this method through
            // ServoDelegatedMotionDriver::InvalidateAxisToken.
            //
            // INVARIANT: every new AxisServo private cancellation-state
            // field added in the future MUST be added to this reset.
            // Otherwise external resets (Phase 0' / torque disable / any
            // future cancellation caller) will leave stale state that the
            // next Update() may act on, regressing the Issue #160 fix.
            void OnExternalReset() {
                pending_dispatch_ = false;
                dispatch_failures_ = 0;
                readmove_failures_ = 0;
            }

        private:
            // Returns true if WritePos was actually issued on the bus
            // (i.e. the snapshot was still the live request at the
            // pre-WritePos freshness gate). Returns false when a newer
            // StartMove superseded the snapshot between Update's
            // motion_mutex_ release and Dispatch's freshness gate — in
            // that case the bus was not touched, and the wrapper Tick()
            // does not need to hold scs_bus_mutex_ across the
            // inter-frame gap.
            bool Dispatch(const AxisMotion& snapshot) {
                int result = 0;
                bool live = false;
                int pos = deg_to_pos_(snapshot.target_deg);
                uint16_t duration =
                    static_cast<uint16_t>(snapshot.move_duration_ms);

                xSemaphoreTake(scs_bus_mutex_, portMAX_DELAY);
                xSemaphoreTake(motion_mutex_, portMAX_DELAY);
                live = motion_.request_token == snapshot.request_token;
                xSemaphoreGive(motion_mutex_);
                if (live) {
                    result = scs_bus_.WritePos(servo_id_, pos, duration, 0);
                    // Hold scs_bus_mutex_ across the post-WritePos quiet
                    // period atomically with the WritePos itself. Without
                    // this, releasing the mutex here would expose a
                    // release/reacquire window where concurrent MCP
                    // callers (get_head_angles ReadPos, uart_diag raw
                    // frames) could acquire the bus and inject traffic
                    // before any wrapper-level quiet-period guard
                    // starts. The original PR #146 bundled critical
                    // section incidentally protected this window;
                    // Phase 2's per-axis short-hold grain restores it
                    // per axis instead. Skipped when the quiet gap is
                    // 0 ms (pitch axis) or the WritePos was superseded
                    // (!live) — see post_dispatch_quiet_gap_ms_ member
                    // comment for per-axis policy rationale.
                    if (post_dispatch_quiet_gap_ms_ > 0) {
                        vTaskDelay(pdMS_TO_TICKS(post_dispatch_quiet_gap_ms_));
                    }
                }
                xSemaphoreGive(scs_bus_mutex_);

                bool write_ok = !live || ServoWritePosOk(result);
                if (live && !write_ok) {
                    ESP_LOGW(TAG,
                             "Motion %s WritePos failed: r=%d (deg=%d, pos=%d)",
                             axis_name_, result, snapshot.target_deg, pos);
                }

                // dispatch_now_ms captures the time WritePos completed
                // (ACK or timeout). FinishDispatch uses this for
                // dispatch_start_ms on success, so ApplyReadMoveResult
                // measures from physical acceptance rather than staging.
                uint32_t dispatch_now_ms =
                    static_cast<uint32_t>(esp_timer_get_time() / 1000);

                xSemaphoreTake(motion_mutex_, portMAX_DELAY);
                // Commit / consume pending only if the snapshot is still
                // live and the WritePos actually ran. Superseded dispatches
                // keep the new request's pending flag intact and reset only
                // the stale retry counter.
                if (live && motion_.request_token == snapshot.request_token) {
                    FinishDispatch(snapshot.target_deg, write_ok,
                                   dispatch_now_ms);
                } else {
                    dispatch_failures_ = 0;
                }
                xSemaphoreGive(motion_mutex_);

                return live;
            }

            void PollReadMove(const AxisMotion& snapshot) {
                int read_move = -1;
                xSemaphoreTake(scs_bus_mutex_, portMAX_DELAY);
                read_move = scs_bus_.ReadMove(servo_id_);
                // Hold scs_bus_mutex_ across the post-ReadMove quiet
                // period atomically with the ReadMove itself, mirroring
                // the post-WritePos pattern in Dispatch(). Without this,
                // releasing the mutex here would expose a window where
                // the wrapper Tick()'s subsequent pitch_axis_.Update()
                // could enter Dispatch() and issue a pitch WritePos
                // immediately, creating a yaw-ReadMove -> pitch-WritePos
                // sequence with effectively 0 ms inter-frame spacing on
                // the shared SCS bus. PR #146's bundled critical section
                // separated dispatch ticks from poll ticks (Tick step 1
                // vs step 2 mutually exclusive), so this ReadMove ->
                // WritePos ordering never arose; Phase 2's per-axis
                // grain makes it possible, so the guard is restored
                // per axis here. Skipped when post_dispatch_quiet_gap_ms_
                // is 0 (pitch axis); the member is shared between
                // post-WritePos and post-ReadMove paths because the
                // bus-quiet rationale is identical for both frame types.
                if (post_dispatch_quiet_gap_ms_ > 0) {
                    vTaskDelay(pdMS_TO_TICKS(post_dispatch_quiet_gap_ms_));
                }
                xSemaphoreGive(scs_bus_mutex_);

                uint32_t now_ms =
                    static_cast<uint32_t>(esp_timer_get_time() / 1000);

                xSemaphoreTake(motion_mutex_, portMAX_DELAY);
                // request_token guards against a newer StartMove racing in
                // between the Update snapshot and this commit; ms-resolution
                // move_start_ms can collide for back-to-back requests.
                if (motion_.request_token == snapshot.request_token) {
                    ApplyReadMoveResult(read_move, now_ms);
                }
                xSemaphoreGive(motion_mutex_);
            }

            // Finalises a dispatched WritePos. Caller holds motion_mutex_.
            // - write_ok==true: commit current_deg = target, consume the
            //   pending_dispatch flag, reset dispatch + ReadMove failure
            //   counters. ReadMove poll then tracks the in-flight delegated
            //   motion to completion.
            // - write_ok==false: keep pending_dispatch=true so the next tick
            //   retries the same target (transient ACK timeout / UART error
            //   should not silently drop the request). Bound the retry by
            //   kDispatchFailureLimit; when exhausted, log once, consume
            //   pending_dispatch, and clear moving so the axis returns to
            //   idle rather than spinning the retry loop forever.
            // readmove_failures_ is reset on success only; it tracks ReadMove
            // polling and is independent of WritePos ack semantics.
            void FinishDispatch(int target_deg, bool write_ok,
                                uint32_t dispatch_now_ms) {
                if (write_ok) {
                    motion_.current_deg = target_deg;
                    // dispatch_start_ms records when the servo actually
                    // received the GOAL_POSITION / GOAL_TIME write. This
                    // (not the staging timestamp in move_start_ms) is what
                    // ApplyReadMoveResult uses for the stuck-high timeout,
                    // so degraded-bus dispatch latency doesn't eat into
                    // the servo-internal duration budget.
                    motion_.dispatch_start_ms = dispatch_now_ms;
                    // Confirmed WritePos ACK supersedes any prior
                    // position_unknown mark. The new WritePos+ReadMove
                    // cycle is what proves (or fails to prove) the
                    // physical position.
                    motion_.position_unknown = false;
                    pending_dispatch_ = false;
                    dispatch_failures_ = 0;
                    readmove_failures_ = 0;
                    return;
                }
                dispatch_failures_++;
                if (dispatch_failures_ >= kDispatchFailureLimit) {
                    ESP_LOGW(TAG,
                             "Motion %s WritePos retries exhausted: current_deg=%d target_deg=%d; %d consecutive dispatch failures, abandoning request and marking position unknown",
                             axis_name_, motion_.current_deg,
                             motion_.target_deg, kDispatchFailureLimit);
                    pending_dispatch_ = false;
                    motion_.moving = false;
                    // A WritePos ACK timeout is NOT proof that the servo
                    // ignored the command — the command may have reached
                    // the servo while only the ACK/readback path failed.
                    // In that case the physical head has already moved to
                    // target_deg, but current_deg still holds the old
                    // value. Without position_unknown=true here, the next
                    // same-old-position StartMove would no-op-skip on the
                    // stale current_deg and silently drop the recovery
                    // request — exactly the degraded-bus condition this
                    // path is meant to handle. Mark unknown so a same-
                    // target retry forces a fresh dispatch and either
                    // confirms (FinishDispatch write_ok clears the flag)
                    // or surfaces another failure.
                    motion_.position_unknown = true;
                    dispatch_failures_ = 0;
                }
                // else: pending_dispatch stays true; next Update will retry the
                // same target (same start_deg / move_start_ms / move_duration_ms).
            }

            void ApplyReadMoveResult(int read_move, uint32_t now_ms) {
                if (read_move >= 0) {
                    readmove_failures_ = 0;
                    if (read_move == 0) {
                        // Sanity check against a stuck-low / false-zero
                        // status register: FinishDispatch optimistically
                        // committed current_deg to target_deg on WritePos
                        // ACK, so a transient ReadMove==0 returned before
                        // the servo could physically reach target would
                        // make the host treat the axis as "at target" and
                        // let the next same-target StartMove no-op-skip.
                        // If the elapsed time since confirmed dispatch is
                        // implausibly short relative to the commanded
                        // move_duration_ms (allowing kReadMoveEarlyMarginMs
                        // for genuine early arrival), treat the zero as
                        // suspicious and mark the position unknown.
                        if (motion_.dispatch_start_ms != 0 &&
                            motion_.move_duration_ms > kReadMoveEarlyMarginMs) {
                            uint32_t elapsed = now_ms - motion_.dispatch_start_ms;
                            uint32_t plausible_min =
                                motion_.move_duration_ms - kReadMoveEarlyMarginMs;
                            if (elapsed < plausible_min) {
                                ESP_LOGW(TAG,
                                         "Motion %s ReadMove=0 implausibly early: current_deg=%d target_deg=%d, elapsed=%ums but commanded duration=%ums (early margin=%ums); marking position unknown",
                                         axis_name_, motion_.current_deg,
                                         motion_.target_deg, (unsigned)elapsed,
                                         (unsigned)motion_.move_duration_ms,
                                         (unsigned)kReadMoveEarlyMarginMs);
                                motion_.moving = false;
                                motion_.position_unknown = true;
                                return;
                            }
                        }
                        motion_.moving = false;
                        return;
                    }
                    // read_move > 0: servo reports still moving. Bound the
                    // wait by move_duration_ms + kReadMoveStuckMarginMs to
                    // guard against a stuck-high ReadMove (the servo or
                    // register path degrades such that the motion-status
                    // bit never clears even after the requested completion
                    // time has elapsed).
                    //
                    // Elapsed is measured from dispatch_start_ms (when the
                    // servo actually received the command via a successful
                    // WritePos ACK), not from move_start_ms (staging time),
                    // so degraded-bus dispatch latency does not cause
                    // premature force-clear while the servo is genuinely
                    // still mid-motion. If dispatch_start_ms is still 0 the
                    // WritePos has not yet ACK'd; skip the timeout check
                    // until the dispatch is confirmed. Unsigned subtraction
                    // stays wrap-safe across the uint32 ms counter.
                    if (motion_.dispatch_start_ms == 0) {
                        return;
                    }
                    uint32_t elapsed = now_ms - motion_.dispatch_start_ms;
                    if (elapsed > motion_.move_duration_ms + kReadMoveStuckMarginMs) {
                        ESP_LOGW(TAG,
                                 "Motion %s ReadMove stuck-high: current_deg=%d target_deg=%d, %ums past commanded completion; marking position unknown and force-clearing moving",
                                 axis_name_, motion_.current_deg,
                                 motion_.target_deg,
                                 (unsigned)(elapsed - motion_.move_duration_ms));
                        motion_.moving = false;
                        motion_.position_unknown = true;
                    }
                    return;
                }

                readmove_failures_++;
                if (readmove_failures_ >= kReadMoveFailureLimit) {
                    ESP_LOGW(TAG,
                             "Motion %s ReadMove failed: current_deg=%d target_deg=%d; %d consecutive ReadMove failures, marking position unknown and force-clearing moving (next StartMove will re-dispatch even if target matches current_deg)",
                             axis_name_, motion_.current_deg,
                             motion_.target_deg, kReadMoveFailureLimit);
                    motion_.moving = false;
                    // Without this flag, a subsequent same-target StartMove
                    // would no-op-skip on current_deg==target_deg and the
                    // bus failure would stay hidden behind the optimistic
                    // commit. Marking the position unknown forces the next
                    // StartMove to re-dispatch and surface (or recover from)
                    // the underlying ReadMove fault.
                    motion_.position_unknown = true;
                    readmove_failures_ = 0;
                }
            }

            uint8_t servo_id_;
            int (*deg_to_pos_)(int);
            const char* axis_name_;
            AxisMotion& motion_;
            ScsBus& scs_bus_;
            SemaphoreHandle_t& scs_bus_mutex_;
            SemaphoreHandle_t& motion_mutex_;
            uint64_t& next_request_token_;
            // Post-bus-frame quiet period held INSIDE scs_bus_mutex_ on
            // a successful bus operation. Applies to BOTH frame types:
            // - Dispatch() WritePos: scs_bus_mutex_ is not released between
            //   the WritePos and this vTaskDelay.
            // - PollReadMove() ReadMove: scs_bus_mutex_ is not released
            //   between the ReadMove and this vTaskDelay.
            //
            // yaw is configured with 10 ms to preserve the SCS bus quiet
            // period that the original PR #146 bundled critical section
            // incidentally protected — for WritePos -> next-frame ordering
            // (PR #146 empirical model) AND for the new ReadMove ->
            // pitch-WritePos ordering introduced by Phase 2's per-axis
            // grain (PR #146 had no such ordering because dispatch and
            // poll Tick phases were mutually exclusive).
            //
            // pitch is configured with 0 ms because the PR #146 empirical
            // model (E1 / E4-fresh / E5 / E6 all clean) shows post-pitch
            // quiet was not required for bus stability. Set to 0 to
            // disable the per-axis post-frame hold entirely. Holding
            // scs_bus_mutex_ across a vTaskDelay is intentional here —
            // it blocks concurrent MCP bus callers (get_head_angles
            // ReadPos, uart_diag raw frames) for the quiet-period
            // duration, which is the explicit invariant being restored.
            //
            // Name retained as "post_dispatch_quiet_gap_ms_" for
            // historical continuity; semantically it is "post-bus-frame
            // quiet gap" and applies symmetrically to both WritePos and
            // ReadMove paths.
            uint32_t post_dispatch_quiet_gap_ms_;
            int readmove_failures_ = 0;
            bool pending_dispatch_ = false;
            int dispatch_failures_ = 0;
        };

        SemaphoreHandle_t& motion_mutex_;
        AxisMotion& yaw_motion_;
        AxisMotion& pitch_motion_;
        // Monotonically increasing request id. Each StartMove that stages
        // a dispatch picks ++next_request_token_ and writes it into the
        // corresponding AxisMotion::request_token. Tick() then uses
        // request_token equality (rather than move_start_ms, which only
        // has ms resolution) to detect whether a snapshot is still the
        // live request. motion_mutex_ guards this counter.
        uint64_t next_request_token_ = 0;
        AxisServo yaw_axis_;
        AxisServo pitch_axis_;
    };

    static size_t ScreenSaverWeatherIconIndex(int code) {
        if (code == 100) return 0;
        if (code == 150) return 1;
        if (code >= 200 && code <= 213) return 2;
        if (code >= 101 && code <= 103) return 3;
        if (code >= 151 && code <= 153) return 4;
        if (code == 104 || code == 154) return 5;
        if (code == 300 || code == 301) return 6;
        if (code == 350 || code == 351) return 7;
        if (code == 302 || code == 303) return 8;
        if (code == 304) return 9;
        if (code == 305 || code == 306 || code == 309 || code == 314) {
            return 10;
        }
        if (code == 307 || code == 308 ||
            (code >= 310 && code <= 312) ||
            (code >= 315 && code <= 318) || code == 399) {
            return 11;
        }
        if (code == 313 || (code >= 404 && code <= 406) || code == 456) {
            return 13;
        }
        if ((code >= 400 && code <= 410) ||
            code == 457 || code == 499) {
            return 12;
        }
        if (code == 500 || code == 501 || code == 509 || code == 510 ||
            code == 514 || code == 515) {
            return 14;
        }
        if (code == 502 || (code >= 511 && code <= 513)) return 15;
        if (code == 503 || code == 504 || code == 507 || code == 508) {
            return 16;
        }
        if (code == 900) return 17;
        if (code == 901) return 18;
        return 19;
    }

    const lv_image_dsc_t* ScreenSaverWeatherIcon(int code) const {
        return &screensaver_weather_icons_[ScreenSaverWeatherIconIndex(code)];
    }

    bool LoadScreenSaverResourcesLocked() {
        if (screensaver_resources_ready_) return true;

        auto& assets = Assets::GetInstance();
        auto load_font = [&assets](
                             const char* name,
                             LvglBinFontPtr& font) {
            void* data = nullptr;
            size_t size = 0;
            if (!assets.GetAssetData(name, data, size) || size == 0 ||
                size > std::numeric_limits<uint32_t>::max()) {
                ESP_LOGE(TAG, "Missing screensaver font asset: %s", name);
                return false;
            }
            LvglBinFontPtr loaded(lv_binfont_create_from_buffer(
                data, static_cast<uint32_t>(size)));
            if (loaded == nullptr) {
                ESP_LOGE(TAG, "Invalid screensaver font asset: %s", name);
                return false;
            }
            font = std::move(loaded);
            return true;
        };

        LvglBinFontPtr digits_font;
        LvglBinFontPtr weather_font;
        LvglBinFontPtr date_font;
        if (!load_font(kScreenSaverDigitsFontAsset, digits_font) ||
            !load_font(kScreenSaverWeatherFontAsset, weather_font) ||
            !load_font(kScreenSaverDateFontAsset, date_font)) {
            return false;
        }

        std::array<lv_image_dsc_t, kScreenSaverWeatherIconCount> icons = {};
        for (size_t index = 0; index < icons.size(); ++index) {
            void* data = nullptr;
            size_t size = 0;
            const char* name = kScreenSaverWeatherIconAssets[index];
            if (!assets.GetAssetData(name, data, size) ||
                size != kScreenSaverWeatherIconBytes) {
                ESP_LOGE(
                    TAG, "Invalid screensaver icon asset: %s (%u bytes)",
                    name, static_cast<unsigned>(size));
                return false;
            }
            auto& icon = icons[index];
            icon.header.magic = LV_IMAGE_HEADER_MAGIC;
            icon.header.cf = LV_COLOR_FORMAT_RGB565A8;
            icon.header.flags = 0;
            icon.header.w = 80;
            icon.header.h = 64;
            icon.header.stride = 160;
            icon.data_size = size;
            icon.data = static_cast<const uint8_t*>(data);
        }

        screensaver_digits_font_ = std::move(digits_font);
        screensaver_weather_font_ = std::move(weather_font);
        screensaver_date_font_ = std::move(date_font);
        screensaver_weather_icons_ = icons;
        screensaver_resources_ready_ = true;
        return true;
    }

    void ResetScreenSaverResourcesLocked() {
        screensaver_visible_.store(false, std::memory_order_release);
        if (screensaver_ != nullptr && lv_obj_is_valid(screensaver_)) {
            lv_obj_del(screensaver_);
        }
        screensaver_ = nullptr;
        screensaver_hour_ = nullptr;
        screensaver_minute_ = nullptr;
        screensaver_weather_icon_ = nullptr;
        screensaver_weather_caption_ = nullptr;
        screensaver_date_ = nullptr;
        screensaver_temperature_ = nullptr;
        screensaver_digits_font_.reset();
        screensaver_weather_font_.reset();
        screensaver_date_font_.reset();
        screensaver_weather_icons_ = {};
        screensaver_resources_ready_ = false;
    }

    lv_obj_t* CreateScreenSaverLabelLocked(
        lv_obj_t* parent, const lv_font_t* font, int x, int y,
        int width, int height,
        lv_label_long_mode_t long_mode = LV_LABEL_LONG_CLIP) {
        lv_obj_t* label = lv_label_create(parent);
        const int line_height = font->line_height;
        lv_obj_set_pos(label, x, y + (height - line_height) / 2);
        lv_obj_set_size(label, width, line_height);
        lv_obj_set_style_text_font(label, font, 0);
        lv_obj_set_style_text_color(label, lv_color_white(), 0);
        lv_obj_set_style_text_align(label, LV_TEXT_ALIGN_CENTER, 0);
        lv_obj_set_style_bg_opa(label, LV_OPA_TRANSP, 0);
        lv_label_set_long_mode(label, long_mode);
        return label;
    }

    bool EnsureScreenSaverLocked() {
        if (screensaver_ != nullptr && lv_obj_is_valid(screensaver_)) {
            return true;
        }
        if (!LoadScreenSaverResourcesLocked()) return false;

        screensaver_ = nullptr;
        lv_obj_t* screen = lv_screen_active();
        if (screen == nullptr) return false;

        screensaver_ = lv_obj_create(screen);
        lv_obj_set_size(screensaver_, DISPLAY_WIDTH, DISPLAY_HEIGHT);
        lv_obj_set_pos(screensaver_, 0, 0);
        lv_obj_clear_flag(screensaver_, LV_OBJ_FLAG_SCROLLABLE);
        lv_obj_set_style_radius(screensaver_, 0, 0);
        lv_obj_set_style_border_width(screensaver_, 0, 0);
        lv_obj_set_style_pad_all(screensaver_, 0, 0);
        lv_obj_set_style_bg_color(screensaver_, lv_color_hex(0x030406), 0);
        lv_obj_set_style_bg_opa(screensaver_, LV_OPA_COVER, 0);

        // Approved v6 grid: one centered 296x216 group with 12 px margins.
        screensaver_hour_ = CreateScreenSaverLabelLocked(
            screensaver_, screensaver_digits_font_.get(),
            12, 12, 152, 104);
        screensaver_minute_ = CreateScreenSaverLabelLocked(
            screensaver_, screensaver_digits_font_.get(),
            12, 124, 152, 104);

        screensaver_weather_icon_ = lv_image_create(screensaver_);
        lv_obj_set_size(screensaver_weather_icon_, 80, 64);
        lv_obj_set_pos(screensaver_weather_icon_, 202, 14);
        lv_obj_clear_flag(
            screensaver_weather_icon_, LV_OBJ_FLAG_SCROLLABLE);

        screensaver_weather_caption_ = CreateScreenSaverLabelLocked(
            screensaver_, screensaver_weather_font_.get(),
            176, 84, 132, 32, LV_LABEL_LONG_SCROLL_CIRCULAR);
        screensaver_temperature_ = CreateScreenSaverLabelLocked(
            screensaver_, screensaver_weather_font_.get(),
            176, 124, 132, 32);
        screensaver_date_ = CreateScreenSaverLabelLocked(
            screensaver_, screensaver_date_font_.get(),
            176, 160, 132, 68);

        lv_obj_add_flag(screensaver_, LV_OBJ_FLAG_HIDDEN);
        return true;
    }

    void PrepareScreenSaver() {
        if (display_ == nullptr) return;
        DisplayLockGuard lock(display_);
        if (EnsureScreenSaverLocked()) {
            HideScreenSaverLocked();
        }
    }

    void UpdateScreenSaverLocked() {
        if (screensaver_ == nullptr || !lv_obj_is_valid(screensaver_)) {
            return;
        }

        time_t now = time(nullptr);
        struct tm local_time = {};
        if (now > 100000 && localtime_r(&now, &local_time) != nullptr) {
            char hour[3];
            char minute[3];
            char date[6];
            std::strftime(hour, sizeof(hour), "%H", &local_time);
            std::strftime(minute, sizeof(minute), "%M", &local_time);
            std::strftime(date, sizeof(date), "%m/%d", &local_time);
            lv_label_set_text(screensaver_hour_, hour);
            lv_label_set_text(screensaver_minute_, minute);
            lv_label_set_text(screensaver_date_, date);
        } else {
            lv_label_set_text(screensaver_hour_, "");
            lv_label_set_text(screensaver_minute_, "");
            lv_label_set_text(screensaver_date_, "");
        }

        char temperature[16];
        if (weather_available_) {
            lv_image_set_src(
                screensaver_weather_icon_,
                ScreenSaverWeatherIcon(weather_icon_code_));
            lv_obj_clear_flag(
                screensaver_weather_icon_, LV_OBJ_FLAG_HIDDEN);
            lv_label_set_text(
                screensaver_weather_caption_, weather_summary_.c_str());
            std::snprintf(
                temperature, sizeof(temperature), "%d°C",
                weather_temperature_c_);
        } else {
            lv_obj_add_flag(
                screensaver_weather_icon_, LV_OBJ_FLAG_HIDDEN);
            lv_label_set_text(screensaver_weather_caption_, "");
            std::snprintf(temperature, sizeof(temperature), "--°C");
        }
        lv_label_set_text(screensaver_temperature_, temperature);
    }

    void HideScreenSaverLocked() {
        screensaver_visible_.store(false, std::memory_order_release);
        if (screensaver_ != nullptr && lv_obj_is_valid(screensaver_)) {
            lv_obj_add_flag(screensaver_, LV_OBJ_FLAG_HIDDEN);
        }
    }

    void HideScreenSaver() {
        if (display_ == nullptr) return;
        DisplayLockGuard lock(display_);
        HideScreenSaverLocked();
    }

    bool HandleScreenSaverUserInteraction() {
        const bool was_visible =
            screensaver_visible_.load(std::memory_order_acquire);
        screensaver_last_activity_us_.store(
            esp_timer_get_time(), std::memory_order_release);
        if (was_visible) {
            UpdateDisplayMode(
                Application::GetInstance().GetDeviceState(), false);
        }
        return was_visible;
    }

    void SetOfferPending(bool pending) {
        offer_pending_.store(pending, std::memory_order_release);
        if (pending && expression_active_.load(std::memory_order_acquire) &&
            expression_invocation_.load(std::memory_order_acquire) ==
                ExpressionInvocation::PREVIEW) {
            expression_abort_requested_.store(
                true, std::memory_order_release);
        }
        UpdateDisplayMode(
            Application::GetInstance().GetDeviceState(), false);
    }

    void SetScreenSaverWeather(
        int icon_code, int temperature_c, const std::string& summary) {
        if (display_ == nullptr) return;
        DisplayLockGuard lock(display_);
        weather_icon_code_ = icon_code;
        weather_temperature_c_ = temperature_c;
        weather_summary_ = summary;
        weather_available_ = true;
        UpdateScreenSaverLocked();
    }

    void SetScreenSaverClock(int epoch_seconds) {
        setenv("TZ", "CST-8", 1);
        tzset();
        struct timeval now = {
            .tv_sec = static_cast<time_t>(epoch_seconds),
            .tv_usec = 0,
        };
        settimeofday(&now, nullptr);
        if (display_ == nullptr) return;
        DisplayLockGuard lock(display_);
        UpdateScreenSaverLocked();
    }

    PublicIpLocation ResolvePublicIpLocation() {
        PublicIpLocation location;
        auto http = GetNetwork()->CreateHttp(0);
        http->SetTimeout(8000);
        http->SetHeader("Accept", "application/json");
        if (!http->Open("GET", kPublicIpLocationUrl)) {
            ESP_LOGW(TAG, "Public IP location lookup failed to connect");
            return location;
        }
        if (http->GetStatusCode() != 200) {
            http->Close();
            return location;
        }

        std::string body = http->ReadAll();
        http->Close();
        cJSON* payload = cJSON_Parse(body.c_str());
        if (payload == nullptr) return location;

        cJSON* success = cJSON_GetObjectItem(payload, "success");
        cJSON* latitude = cJSON_GetObjectItem(payload, "latitude");
        cJSON* longitude = cJSON_GetObjectItem(payload, "longitude");
        if (cJSON_IsTrue(success) && cJSON_IsNumber(latitude) &&
            cJSON_IsNumber(longitude) &&
            std::isfinite(latitude->valuedouble) &&
            std::isfinite(longitude->valuedouble) &&
            latitude->valuedouble >= -90 &&
            latitude->valuedouble <= 90 &&
            longitude->valuedouble >= -180 &&
            longitude->valuedouble <= 180) {
            location.available = true;
            location.latitude = latitude->valuedouble;
            location.longitude = longitude->valuedouble;
        }
        cJSON_Delete(payload);
        return location;
    }

    void RunPublicIpLocationTask() {
        while (true) {
            std::string ssid;
            {
                std::lock_guard<std::mutex> lock(
                    public_ip_location_mutex_);
                ssid = public_ip_location_ssid_;
            }

            PublicIpLocation location = ResolvePublicIpLocation();
            {
                std::lock_guard<std::mutex> lock(
                    public_ip_location_mutex_);
                if (ssid != public_ip_location_ssid_) {
                    continue;
                }
                public_ip_location_ = location;
                public_ip_location_task_running_ = false;
                if (!location.available) {
                    public_ip_location_ssid_.clear();
                }
            }
            if (location.available) {
                ESP_LOGI(TAG, "Public IP location cached");
            } else {
                ESP_LOGW(TAG, "Public IP location unavailable");
            }
            return;
        }
    }

    static void PublicIpLocationTaskTrampoline(void* arg) {
        auto* board = static_cast<StackChanBoard*>(arg);
        board->RunPublicIpLocationTask();
        vTaskDelete(nullptr);
    }

    void RefreshPublicIpLocationForWifi(const std::string& ssid) {
        if (ssid.empty()) return;

        {
            std::lock_guard<std::mutex> lock(public_ip_location_mutex_);
            if (ssid == public_ip_location_ssid_) return;
            public_ip_location_ssid_ = ssid;
            public_ip_location_ = PublicIpLocation{};
            if (public_ip_location_task_running_) return;
            public_ip_location_task_running_ = true;
        }

        BaseType_t ok = xTaskCreate(
            &StackChanBoard::PublicIpLocationTaskTrampoline,
            "public_ip_loc", 6144, this, tskIDLE_PRIORITY + 1, nullptr);
        if (ok != pdPASS) {
            ESP_LOGE(TAG, "Failed to create public IP location task");
            std::lock_guard<std::mutex> lock(public_ip_location_mutex_);
            public_ip_location_task_running_ = false;
            public_ip_location_ssid_.clear();
            public_ip_location_ = PublicIpLocation{};
        }
    }

    cJSON* GetPublicIpLocation() {
        cJSON* root = cJSON_CreateObject();
        std::lock_guard<std::mutex> lock(public_ip_location_mutex_);
        if (public_ip_location_.available) {
            cJSON_AddNumberToObject(
                root, "latitude", public_ip_location_.latitude);
            cJSON_AddNumberToObject(
                root, "longitude", public_ip_location_.longitude);
        }
        return root;
    }

    void InitializePowerSaveTimer() {
        power_save_timer_ = new PowerSaveTimer(-1, 60, 300);
        power_save_timer_->OnEnterDimMode([this]() {
            GetBacklight()->SetBrightness(10);
        });
        power_save_timer_->OnEnterSleepMode([this]() {
            GetDisplay()->SetPowerSaveMode(true);
            GetBacklight()->SetBrightness(0);
        });
        power_save_timer_->OnExitSleepMode([this]() {
            GetDisplay()->SetPowerSaveMode(false);
            GetBacklight()->RestoreBrightness();
        });
        power_save_timer_->OnShutdownRequest([this]() {
            pmic_->PowerOff();
        });
        // Battery telemetry enables this only while discharging. Starting
        // disabled keeps an externally powered boot awake from the outset.
    }

    void InitializeI2c() {
        // Initialize I2C peripheral
        i2c_master_bus_config_t i2c_bus_cfg = {
            .i2c_port = (i2c_port_t)1,
            .sda_io_num = AUDIO_CODEC_I2C_SDA_PIN,
            .scl_io_num = AUDIO_CODEC_I2C_SCL_PIN,
            .clk_source = I2C_CLK_SRC_DEFAULT,
            .glitch_ignore_cnt = 7,
            .intr_priority = 0,
            .trans_queue_depth = 0,
            .flags = {
                .enable_internal_pullup = 1,
            },
        };
        ESP_ERROR_CHECK(i2c_new_master_bus(&i2c_bus_cfg, &i2c_bus_));
    }

    void InitializePortAI2c() {
        // Grove Port A bus. Uses I2C controller 0 (the internal bus above
        // uses controller 1) so the two run independently. Attached Unit
        // modules typically include their own 10 kΩ pull-ups in the Grove
        // hub, but enable internal pull-ups as a fall-back for bare wiring.
        i2c_master_bus_config_t port_a_cfg = {
            .i2c_port = (i2c_port_t)0,
            .sda_io_num = PORT_A_I2C_SDA_PIN,
            .scl_io_num = PORT_A_I2C_SCL_PIN,
            .clk_source = I2C_CLK_SRC_DEFAULT,
            .glitch_ignore_cnt = 7,
            .intr_priority = 0,
            .trans_queue_depth = 0,
            .flags = {
                .enable_internal_pullup = 1,
            },
        };
        ESP_ERROR_CHECK(i2c_new_master_bus(&port_a_cfg, &port_a_i2c_bus_));
    }

    esp_err_t InitPortBWs2812(uint16_t led_count) {
        if (ws2812_ok_ && ws2812_led_count_ == led_count) {
            return ESP_OK;  // idempotent: same led_count is a no-op
        }
        if (ws2812_handle_ != nullptr) {
            led_strip_del(ws2812_handle_);
            ws2812_handle_ = nullptr;
            ws2812_ok_ = false;
            ws2812_led_count_ = 0;
        }

        led_strip_config_t strip_config = {
            .strip_gpio_num = PORT_B_WS2812_DATA_PIN,
            .max_leds = led_count,
            .led_model = LED_MODEL_WS2812,
            .color_component_format = LED_STRIP_COLOR_COMPONENT_FMT_GRB,
            .flags = { .invert_out = false },
        };
        led_strip_rmt_config_t rmt_config = {
            .clk_src = RMT_CLK_SRC_DEFAULT,
            .resolution_hz = 10 * 1000 * 1000,  // 10 MHz, standard WS2812 bit timing
            .mem_block_symbols = 0,              // 0 = driver default block size
            .flags = { .with_dma = false },
        };
        esp_err_t err = led_strip_new_rmt_device(&strip_config, &rmt_config, &ws2812_handle_);
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "port_b.ws2812.init led_strip_new_rmt_device failed: %s",
                     esp_err_to_name(err));
            return err;
        }

        err = led_strip_clear(ws2812_handle_);
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "port_b.ws2812.init led_strip_clear failed: %s",
                     esp_err_to_name(err));
            led_strip_del(ws2812_handle_);
            ws2812_handle_ = nullptr;
            return err;
        }
        ws2812_led_count_ = led_count;
        ws2812_ok_ = true;
        ESP_LOGI(TAG, "port_b.ws2812 initialized: %u LEDs on GPIO %d",
                 (unsigned)led_count, (int)PORT_B_WS2812_DATA_PIN);
        return ESP_OK;
    }

    esp_err_t InitPortCWs2812(uint16_t led_count) {
        if (port_c_ws2812_ok_ && port_c_ws2812_led_count_ == led_count) {
            return ESP_OK;  // idempotent: same led_count is a no-op
        }
        if (port_c_ws2812_handle_ != nullptr) {
            led_strip_del(port_c_ws2812_handle_);
            port_c_ws2812_handle_ = nullptr;
            port_c_ws2812_ok_ = false;
            port_c_ws2812_led_count_ = 0;
        }

        led_strip_config_t strip_config = {
            .strip_gpio_num = PORT_C_WS2812_DATA_PIN,
            .max_leds = led_count,
            .led_model = LED_MODEL_WS2812,
            .color_component_format = LED_STRIP_COLOR_COMPONENT_FMT_GRB,
            .flags = { .invert_out = false },
        };
        // The led_strip driver auto-allocates an RMT TX channel. ESP32-S3 has
        // multiple TX channels, and StackChan uses RMT only for the Port B/C
        // strips, so both WS2812 handles can coexist.
        led_strip_rmt_config_t rmt_config = {
            .clk_src = RMT_CLK_SRC_DEFAULT,
            .resolution_hz = 10 * 1000 * 1000,  // 10 MHz, standard WS2812 bit timing
            .mem_block_symbols = 0,              // 0 = driver default block size
            .flags = { .with_dma = false },
        };
        esp_err_t err = led_strip_new_rmt_device(&strip_config, &rmt_config, &port_c_ws2812_handle_);
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "port_c.ws2812.init Port C GPIO %d led_strip_new_rmt_device failed: %s",
                     (int)PORT_C_WS2812_DATA_PIN, esp_err_to_name(err));
            return err;
        }

        err = led_strip_clear(port_c_ws2812_handle_);
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "port_c.ws2812.init Port C GPIO %d led_strip_clear failed: %s",
                     (int)PORT_C_WS2812_DATA_PIN, esp_err_to_name(err));
            led_strip_del(port_c_ws2812_handle_);
            port_c_ws2812_handle_ = nullptr;
            return err;
        }
        port_c_ws2812_led_count_ = led_count;
        port_c_ws2812_ok_ = true;
        ESP_LOGI(TAG, "port_c.ws2812 initialized: %u LEDs on Port C GPIO %d",
                 (unsigned)led_count, (int)PORT_C_WS2812_DATA_PIN);
        return ESP_OK;
    }

    void I2cDetect() {
        uint8_t address;
        printf("     0  1  2  3  4  5  6  7  8  9  a  b  c  d  e  f\r\n");
        for (int i = 0; i < 128; i += 16) {
            printf("%02x: ", i);
            for (int j = 0; j < 16; j++) {
                fflush(stdout);
                address = i + j;
                esp_err_t ret = i2c_master_probe(i2c_bus_, address, pdMS_TO_TICKS(200));
                if (ret == ESP_OK) {
                    printf("%02x ", address);
                } else if (ret == ESP_ERR_TIMEOUT) {
                    printf("UU ");
                } else {
                    printf("-- ");
                }
            }
            printf("\r\n");
        }
    }

    void InitializeAxp2101() {
        ESP_LOGI(TAG, "Init AXP2101");
        pmic_ = new Pmic(i2c_bus_, 0x34);
    }

    void InitializeAw9523() {
        ESP_LOGI(TAG, "Init AW9523");
        aw9523_ = new Aw9523(i2c_bus_, 0x58);
        vTaskDelay(pdMS_TO_TICKS(50));
    }

    void EnsureSettingsUiLocked() {
        lv_obj_t* screen = lv_screen_active();
        if (screen == nullptr) {
            return;
        }
        if (settings_panel_ == nullptr) {
            settings_panel_ = lv_obj_create(screen);
            lv_obj_set_size(settings_panel_, DISPLAY_WIDTH, DISPLAY_HEIGHT);
            lv_obj_align(settings_panel_, LV_ALIGN_CENTER, 0, 0);
            lv_obj_set_style_border_width(settings_panel_, 0, 0);
            settings_volume_label_ = lv_label_create(settings_panel_);
            lv_obj_align(settings_volume_label_, LV_ALIGN_CENTER, 0, 0);
            lv_obj_add_flag(settings_panel_, LV_OBJ_FLAG_HIDDEN);
        }
    }

    void UpdateDisplayMode(DeviceState state, bool state_changed) {
        if (display_ == nullptr) {
            return;
        }
        bool settings_open = settings_open_.load(std::memory_order_acquire);
        bool behavior_active =
            physical_behavior_owner_.load(std::memory_order_acquire) !=
                PhysicalBehaviorOwner::IDLE;
        int64_t now_us = esp_timer_get_time();
        if (state != kDeviceStateIdle || settings_open || behavior_active) {
            screensaver_last_activity_us_.store(
                now_us, std::memory_order_release);
        }
        int64_t last_activity_us = screensaver_last_activity_us_.load(
            std::memory_order_acquire);
        bool show_screensaver =
            state == kDeviceStateIdle && !settings_open && !behavior_active &&
            !offer_pending_.load(std::memory_order_acquire) &&
            last_activity_us > 0 &&
            now_us - last_activity_us >= SCREEN_SAVER_IDLE_TIMEOUT_US;
        bool appliance_mode = !show_screensaver &&
            (state == kDeviceStateIdle ||
             state == kDeviceStateListening ||
             state == kDeviceStateSpeaking);

        DisplayLockGuard lock(display_);
        display_->SetApplianceStatusStyleLocked(appliance_mode);
        if (state_changed) {
            display_->SetRecordingIndicatorLocked(
                state == kDeviceStateListening);
        }
        if (show_screensaver && face_image_ != nullptr &&
            lv_obj_is_valid(face_image_) &&
            !lv_obj_has_flag(face_image_, LV_OBJ_FLAG_HIDDEN) &&
            screensaver_ != nullptr && lv_obj_is_valid(screensaver_)) {
            UpdateScreenSaverLocked();
            if (!screensaver_visible_.load(std::memory_order_acquire)) {
                lv_obj_clear_flag(screensaver_, LV_OBJ_FLAG_HIDDEN);
                lv_obj_move_foreground(screensaver_);
            }
            screensaver_visible_.store(true, std::memory_order_release);
        } else {
            HideScreenSaverLocked();
        }
        if (settings_open && settings_panel_ != nullptr &&
            lv_obj_is_valid(settings_panel_)) {
            lv_obj_move_foreground(settings_panel_);
        }
    }

    void OpenVolumeSettings() {
        if (display_ == nullptr) {
            return;
        }
        power_save_timer_->WakeUp();
        DisplayLockGuard lock(display_);
        EnsureSettingsUiLocked();
        if (settings_panel_ == nullptr) {
            return;
        }
        settings_open_.store(true, std::memory_order_release);
        char text[96];
        snprintf(
            text,
            sizeof(text),
            "Volume %d\n\nTap left: -   Tap right: +\nSwipe down to close",
            GetAudioCodec()->output_volume());
        lv_label_set_text(settings_volume_label_, text);
        lv_obj_remove_flag(settings_panel_, LV_OBJ_FLAG_HIDDEN);
        lv_obj_move_foreground(settings_panel_);
    }

    void CloseVolumeSettings() {
        settings_open_.store(false, std::memory_order_release);
        if (display_ == nullptr) {
            return;
        }
        DisplayLockGuard lock(display_);
        if (settings_panel_ != nullptr) {
            lv_obj_add_flag(settings_panel_, LV_OBJ_FLAG_HIDDEN);
        }
    }

    void AdjustVolumeFromSettings(bool increase) {
        int volume = GetAudioCodec()->output_volume();
        volume += increase ? 10 : -10;
        volume = std::clamp(volume, 0, 100);
        GetAudioCodec()->SetOutputVolume(volume);
        OpenVolumeSettings();
    }

    void PollTouchpad() {
        static bool was_touched = false;
        static int64_t touch_start_time = 0;
        static int touch_start_x = -1;
        static int touch_start_y = -1;
        static int touch_last_y = -1;
        static bool touch_woke_screensaver = false;
        static int64_t last_release_ms = 0;       // デバウンス用 (= 直前 release 時刻)
        static int64_t listening_started_ms = 0;  // タイムアウト用 (= listening 突入時刻)
        static bool was_listening = false;        // listening 突入のエッジ検出
        const int64_t TOUCH_THRESHOLD_MS = 500;   // 触摸时长阈值，超过500ms视为长按
        const int64_t DEBOUNCE_MS = 300;          // 直前 release から N ms 以内の press は無視
        const int64_t LISTEN_TIMEOUT_MS = 30000;  // listening 状態に N ms 以上滞在で auto stop
        const int EAR_TOUCH_TOP = 34;
        const int EAR_TOUCH_BOTTOM = 205;
        const int LEFT_EAR_TOUCH_RIGHT = 85;
        const int RIGHT_EAR_TOUCH_LEFT = 235;

        auto& app = Application::GetInstance();
        int64_t now_ms = esp_timer_get_time() / 1000;
        static int64_t last_screensaver_update_ms = 0;
        if (now_ms - last_screensaver_update_ms >= 1000) {
            last_screensaver_update_ms = now_ms;
            UpdateDisplayMode(app.GetDeviceState(), false);
        }

        // --- listening 状態の上界 (タイムアウト) 管理 ---
        // 状態遷移のエッジ検出で突入時刻を記録、 滞在時間が LISTEN_TIMEOUT_MS を
        // 超えたら StopListening を自動発火する。 タッチ忘れ放置で listen が
        // 無限持続するのを防ぐ。 StopListening 後は listening_started_ms を 0 に
        // 戻して再発火を抑止 (次に listening 突入したら再セット)。
        bool is_listening = (app.GetDeviceState() == kDeviceStateListening);
        if (is_listening && !was_listening) {
            listening_started_ms = now_ms;
            ESP_LOGI(TAG, "Listening entered at %d ms (timeout in %d ms)",
                     (int)now_ms, (int)LISTEN_TIMEOUT_MS);
        }
        was_listening = is_listening;
        if (is_listening && listening_started_ms != 0 &&
            (now_ms - listening_started_ms) > LISTEN_TIMEOUT_MS) {
            ESP_LOGI(TAG, "Listening timeout reached (%d ms) -> StopListening",
                     (int)(now_ms - listening_started_ms));
            SetAllRgbLeds(0, 0, 0);
            app.StopListening();
            listening_started_ms = 0;
        }

        ft6336_->UpdateTouchPoint();
        auto& touch_point = ft6336_->GetTouchPoint();
        if (touch_point.num > 0) {
            power_save_timer_->WakeUp();
        }

        // 检测触摸开始
        if (touch_point.num > 0 && !was_touched) {
            // デバウンス: 直前 release から DEBOUNCE_MS 以内の press は無視。
            // FT6336 のチャタリングや「タッチした直後にもう一度触れてしまう」
            // 連打事故を防止。
            if (last_release_ms != 0 && (now_ms - last_release_ms) < DEBOUNCE_MS) {
                // was_touched は更新しない。 次の poll でも press 判定を再評価
                // するが、 デバウンス期間を超えれば通常 press として処理される。
                return;
            }
            was_touched = true;
            touch_woke_screensaver =
                HandleScreenSaverUserInteraction();
            touch_start_time = now_ms;
            touch_start_x = touch_point.x;
            touch_start_y = touch_point.y;
            touch_last_y = touch_point.y;
            // タッチ瞬時の PlaySound 直接呼び出しは行わない。 直後に
            // StartListening → EnableVoiceProcessing(true) → ResetDecoder で
            // playback queue がクリアされて音が消えるため。 代わりに
            // Application::StartListening 側で play_popup_on_listening_ flag を
            // 立てて、 HandleStateChangedEvent の Listening 分岐後半 (ResetDecoder
            // の後) で OGG_POPUP を鳴らす経路に乗せる (= xiaozhi 標準の WakeWord
            // 経路と同じ仕組み)。
        }
        else if (touch_point.num > 0 && was_touched) {
            touch_last_y = touch_point.y;
        }
        // 检测触摸释放
        else if (touch_point.num == 0 && was_touched) {
            was_touched = false;
            int64_t touch_duration = now_ms - touch_start_time;
            int touch_end_y = touch_last_y;
            last_release_ms = now_ms;
            if (touch_woke_screensaver) {
                touch_woke_screensaver = false;
                return;
            }

            const bool settings_gesture = touch_start_y >= 0 &&
                touch_start_y - touch_end_y > 50;
            const bool right_ear_gesture =
                touch_duration < TOUCH_THRESHOLD_MS &&
                touch_start_y >= EAR_TOUCH_TOP &&
                touch_start_y <= EAR_TOUCH_BOTTOM &&
                touch_start_x >= RIGHT_EAR_TOUCH_LEFT;
            if (expression_active_.load(std::memory_order_acquire) &&
                (settings_gesture || right_ear_gesture)) {
                expression_abort_requested_.store(
                    true, std::memory_order_release);
                return;
            }

            if (settings_open_.load(std::memory_order_acquire)) {
                if (touch_start_y >= 0 && touch_end_y - touch_start_y > 50) {
                    CloseVolumeSettings();
                } else if (touch_duration < TOUCH_THRESHOLD_MS) {
                    AdjustVolumeFromSettings(
                        touch_start_x >= DISPLAY_WIDTH / 2);
                }
                return;
            }
            if (
                app.GetDeviceState() == kDeviceStateIdle &&
                touch_start_y >= 0 &&
                touch_start_y - touch_end_y > 50
            ) {
                OpenVolumeSettings();
                return;
            }

            // 只有短触才触发
            if (touch_duration < TOUCH_THRESHOLD_MS) {
                if (app.GetDeviceState() == kDeviceStateWifiConfiguring) {
                    bool enabled = touch_start_x >= DISPLAY_WIDTH / 2;
                    app.Schedule([enabled]() {
                        Ota::SetAutomaticUpdatesEnabled(enabled);
                        auto display = Board::GetInstance().GetDisplay();
                        display->ShowNotification(
                            enabled
                                ? "Automatic OTA enabled"
                                : "Automatic OTA disabled",
                            3000);
                    });
                    return;
                }
                if (app.GetDeviceState() == kDeviceStateStarting) {
                    EnterWifiConfigMode();
                    return;
                }
                // kDeviceStateAudioTesting は WiFi config 完了直後の audio test
                // モードに居る状態。 ここから WifiConfiguring に戻る経路は
                // ToggleChatState() しか持っていない (= HandleStartListeningEvent
                // は AudioTesting を扱わない)。 StartListening にだけ分岐すると
                // タッチで設定モードに復帰できなくなるので、 AudioTesting だけ
                // は従来通り ToggleChatState() に流して状態機械任せにする。
                if (app.GetDeviceState() == kDeviceStateAudioTesting) {
                    app.ToggleChatState();
                    return;
                }
                bool in_ear_row = touch_start_y >= EAR_TOUCH_TOP &&
                    touch_start_y <= EAR_TOUCH_BOTTOM;
                bool left_ear = in_ear_row &&
                    touch_start_x < LEFT_EAR_TOUCH_RIGHT;
                bool right_ear = in_ear_row &&
                    touch_start_x >= RIGHT_EAR_TOUCH_LEFT;

                if (app.GetDeviceState() == kDeviceStateListening) {
                    if (left_ear || right_ear) {
                        SetAllRgbLeds(0, 0, 0);
                    }
                    if (left_ear) {
                        app.CancelListening();
                    } else if (right_ear) {
                        app.StopListening();
                    }
                } else if (right_ear) {
                    SetAllRgbLeds(0, 32, 0);
                    app.StartListening();
                }
            }
        }
    }

    void InitializeFt6336TouchPad() {
        ESP_LOGI(TAG, "Init FT6336");
        ft6336_ = new Ft6336(i2c_bus_, 0x38);
        
        // 创建定时器，20ms 间隔
        esp_timer_create_args_t timer_args = {
            .callback = [](void* arg) {
                StackChanBoard* board = (StackChanBoard*)arg;
                board->PollTouchpad();
            },
            .arg = this,
            .dispatch_method = ESP_TIMER_TASK,
            .name = "touchpad_timer",
            .skip_unhandled_events = true,
        };
        
        ESP_ERROR_CHECK(esp_timer_create(&timer_args, &touchpad_timer_));
        ESP_ERROR_CHECK(esp_timer_start_periodic(touchpad_timer_, 20 * 1000));
    }

    void InitializeSpi() {
        spi_bus_config_t buscfg = {};
        buscfg.mosi_io_num = GPIO_NUM_37;
        buscfg.miso_io_num = GPIO_NUM_NC;
        buscfg.sclk_io_num = GPIO_NUM_36;
        buscfg.quadwp_io_num = GPIO_NUM_NC;
        buscfg.quadhd_io_num = GPIO_NUM_NC;
        buscfg.max_transfer_sz = DISPLAY_WIDTH * DISPLAY_HEIGHT * sizeof(uint16_t);
        ESP_ERROR_CHECK(spi_bus_initialize(SPI3_HOST, &buscfg, SPI_DMA_CH_AUTO));
    }

    void InitializeIli9342Display() {
        ESP_LOGI(TAG, "Init IlI9342");

        esp_lcd_panel_io_handle_t panel_io = nullptr;
        esp_lcd_panel_handle_t panel = nullptr;

        ESP_LOGD(TAG, "Install panel IO");
        esp_lcd_panel_io_spi_config_t io_config = {};
        io_config.cs_gpio_num = GPIO_NUM_3;
        io_config.dc_gpio_num = GPIO_NUM_35;
        io_config.spi_mode = 2;
        io_config.pclk_hz = 40 * 1000 * 1000;
        io_config.trans_queue_depth = 10;
        io_config.lcd_cmd_bits = 8;
        io_config.lcd_param_bits = 8;
        ESP_ERROR_CHECK(esp_lcd_new_panel_io_spi(SPI3_HOST, &io_config, &panel_io));

        ESP_LOGD(TAG, "Install LCD driver");
        esp_lcd_panel_dev_config_t panel_config = {};
        panel_config.reset_gpio_num = GPIO_NUM_NC;
        panel_config.rgb_ele_order = LCD_RGB_ELEMENT_ORDER_BGR;
        panel_config.bits_per_pixel = 16;
        ESP_ERROR_CHECK(esp_lcd_new_panel_ili9341(panel_io, &panel_config, &panel));
        
        esp_lcd_panel_reset(panel);
        aw9523_->ResetIli9342();

        esp_lcd_panel_init(panel);
        esp_lcd_panel_invert_color(panel, true);
        esp_lcd_panel_swap_xy(panel, DISPLAY_SWAP_XY);
        esp_lcd_panel_mirror(panel, DISPLAY_MIRROR_X, DISPLAY_MIRROR_Y);

        display_ = new SpiLcdDisplay(panel_io, panel,
                                    DISPLAY_WIDTH, DISPLAY_HEIGHT, DISPLAY_OFFSET_X, DISPLAY_OFFSET_Y, DISPLAY_MIRROR_X, DISPLAY_MIRROR_Y, DISPLAY_SWAP_XY);
    }

     void InitializeCamera() {
        static esp_cam_ctlr_dvp_pin_config_t dvp_pin_config = {
            .data_width = CAM_CTLR_DATA_WIDTH_8,
            .data_io = {
                [0] = CAMERA_PIN_D0,
                [1] = CAMERA_PIN_D1,
                [2] = CAMERA_PIN_D2,
                [3] = CAMERA_PIN_D3,
                [4] = CAMERA_PIN_D4,
                [5] = CAMERA_PIN_D5,
                [6] = CAMERA_PIN_D6,
                [7] = CAMERA_PIN_D7,
            },
            .vsync_io = CAMERA_PIN_VSYNC,
            .de_io = CAMERA_PIN_HREF,
            .pclk_io = CAMERA_PIN_PCLK,
            .xclk_io = CAMERA_PIN_XCLK,
        };

        esp_video_init_sccb_config_t sccb_config = {
            .init_sccb = false,
            .i2c_handle = i2c_bus_,
            .freq = 100000,
        };

        esp_video_init_dvp_config_t dvp_config = {
            .sccb_config = sccb_config,
            .reset_pin = CAMERA_PIN_RESET,
            .pwdn_pin = CAMERA_PIN_PWDN,
            .dvp_pin = dvp_pin_config,
            .xclk_freq = XCLK_FREQ_HZ,
        };

        esp_video_init_config_t video_config = {
            .dvp = &dvp_config,
        };

        camera_ = new EspVideo(video_config);
        camera_->SetHMirror(false);
    }

    bool servo_ok_ = false;
    bool rgb_ok_ = false;
    static constexpr uint8_t RGB_LED_COUNT = 12;  // StackChan base has 12 WS2812C
    static constexpr uint8_t RGB_DATA_PIN  = 13;  // PY32 expander pin (not ESP32 GPIO)

    void InitializeIOExpander() {
        ESP_LOGI(TAG, "Init PY32 IO expander (I2C addr 0x%02X)", Py32IoExpander::DEFAULT_ADDR);
        io_expander_ = std::unique_ptr<Py32IoExpander>(new Py32IoExpander(i2c_bus_));

        // PY32 boots slowly and is unreliable in the first few hundred ms
        // after power-on. Retry the probe up to 5 times with 500ms gaps —
        // total budget ~2.5 s, which dominates boot latency by maybe 1.5 s
        // in the worst case but is still well under the time spent on
        // I2C scan + LCD panel init that happen earlier.
        constexpr int kBeginRetries  = 5;
        constexpr int kBeginDelayMs  = 500;
        bool   ok = false;
        uint8_t version = 0;
        int     winning_attempt = 0;
        for (int i = 0; i < kBeginRetries; i++) {
            vTaskDelay(pdMS_TO_TICKS(kBeginDelayMs));
            if (io_expander_->Begin(&version)) {
                ok = true;
                winning_attempt = i + 1;
                break;
            }
            ESP_LOGW(TAG, "PY32 not responding, retry %d/%d", i + 1, kBeginRetries);
        }

        if (!ok) {
            ESP_LOGE(TAG, "PY32 IO expander FAILED after %d attempts; servo will be POWERLESS",
                     kBeginRetries);
            io_expander_.reset();
            return;
        }
        ESP_LOGI(TAG, "PY32 IO expander READY (version=0x%02X, attempt=%d/%d)",
                 version, winning_attempt, kBeginRetries);

        // Pin 0 = VM EN (servo power switch). Output, pull-up, drive HIGH.
        // We track each step so a partial success is reported precisely
        // (e.g. direction set but pull-up failed) — much easier to debug
        // than the previous "all-void, hope it stuck" version.
        bool ok_dir   = io_expander_->SetDirection(0, true);
        bool ok_pull  = io_expander_->SetPullMode(0, true);
        bool ok_write = io_expander_->DigitalWrite(0, true);
        vTaskDelay(pdMS_TO_TICKS(200));

        if (!ok_dir || !ok_pull || !ok_write) {
            const char* failed = "?";
            if (!ok_dir)        failed = "SetDirection";
            else if (!ok_pull)  failed = "SetPullMode";
            else if (!ok_write) failed = "DigitalWrite";
            ESP_LOGE(TAG, "Servo power ENABLE FAILED at step=%s", failed);
            return;
        }

        // Verify by reading back the output low-byte register. Bit 0 must
        // be high. If not, the chip ACK'd but the level didn't latch — log
        // it loudly so we know the next move_head will be silent.
        uint8_t out_low = 0;
        if (io_expander_->ReadOutputLow(&out_low)) {
            if (out_low & 0x01) {
                ESP_LOGI(TAG, "Servo power ENABLED via PY32 pin 0 "
                              "(VM EN HIGH confirmed, REG_GPIO_O_L=0x%02X)", out_low);
            } else {
                ESP_LOGE(TAG, "Servo power write succeeded but readback shows "
                              "pin 0 LOW (REG_GPIO_O_L=0x%02X) — VM EN may be off!",
                              out_low);
            }
        } else {
            // Read failed but writes succeeded; assume the writes took.
            ESP_LOGW(TAG, "Servo power writes OK, but readback verify failed "
                          "(can't confirm VM EN level)");
        }

        // ---- RGB strip init (12x WS2812C on the StackChan base) ----
        // The data line is on PY32 pin 13 (not an ESP32 GPIO); the PY32
        // bit-bangs the WS2812 protocol itself. We just write RGB565 into
        // its LED RAM and toggle the latch bit. Sequence is the same as the
        // M5 BSP: configure pin 13 as push-pull output with pull-up,
        // SetLedCount(12), small settle delay, then clear all LEDs.
        bool ok_d   = io_expander_->SetDirection(RGB_DATA_PIN, true);
        bool ok_p   = io_expander_->SetPullMode(RGB_DATA_PIN, true);
        bool ok_dr  = io_expander_->SetDriveMode(RGB_DATA_PIN, false);
        bool ok_cnt = io_expander_->SetLedCount(RGB_LED_COUNT);
        if (!ok_d || !ok_p || !ok_dr || !ok_cnt) {
            const char* failed = "?";
            if      (!ok_d)   failed = "SetDirection(13)";
            else if (!ok_p)   failed = "SetPullMode(13)";
            else if (!ok_dr) failed = "SetDriveMode(13)";
            else if (!ok_cnt) failed = "SetLedCount";
            ESP_LOGE(TAG, "RGB strip init FAILED at step=%s; LEDs disabled", failed);
            return;
        }
        // M5 reference firmware waits 200 ms after SetLedCount before the
        // first refresh — the PY32 internal LED engine needs the settle.
        vTaskDelay(pdMS_TO_TICKS(200));

        // Clear strip: zero RAM in one burst, then latch.
        uint8_t clear_buf[RGB_LED_COUNT * 2] = {0};
        bool ok_clear = io_expander_->SetLedData(clear_buf, sizeof(clear_buf));
        bool ok_ref   = io_expander_->RefreshLeds();
        if (!ok_clear || !ok_ref) {
            ESP_LOGE(TAG, "RGB strip clear FAILED (data=%d refresh=%d); LEDs disabled",
                     ok_clear, ok_ref);
            return;
        }
        rgb_ok_ = true;
        ESP_LOGI(TAG, "RGB strip READY (%d WS2812C via PY32 pin %d, all cleared)",
                 RGB_LED_COUNT, RGB_DATA_PIN);
    }

    // Helpers for the LED MCP tools below. Centralised so the parsing/
    // clamping logic isn't duplicated in three handlers.
    static uint8_t ClampByte(int v) {
        if (v < 0) return 0;
        if (v > 255) return 255;
        return (uint8_t)v;
    }

    static bool JsonByte(cJSON* item, uint8_t* out) {
        if (!cJSON_IsNumber(item)) return false;
        if (item->valuedouble != static_cast<double>(item->valueint)) return false;
        if (item->valueint < 0 || item->valueint > 255) return false;
        *out = static_cast<uint8_t>(item->valueint);
        return true;
    }

    // Pack one RGB888 sample into the {lo, hi} RGB565 pair the PY32
    // expects in its LED RAM.
    static void PackRgb565(uint8_t r, uint8_t g, uint8_t b, uint8_t out[2]) {
        uint16_t v = (uint16_t)(((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3));
        out[0] = (uint8_t)(v & 0xFF);
        out[1] = (uint8_t)((v >> 8) & 0xFF);
    }

    // 全 RGB LED を同じ色にする helper。 self.led.set_all MCP tool と同じ I2C 経路
    // (PY32 経由 WS2812)。 PollTouchpad のタッチフィードバック等、 MCP 以外の
    // 経路から LED を駆動するときに使う。 PY32 init 失敗時 (rgb_ok_ == false)
    // は no-op で安全に抜ける。
    void SetAllRgbLeds(uint8_t r, uint8_t g, uint8_t b) {
        if (!rgb_ok_ || io_expander_ == nullptr) {
            return;
        }
        uint8_t buf[RGB_LED_COUNT * 2];
        uint8_t pair[2];
        PackRgb565(r, g, b, pair);
        for (int i = 0; i < RGB_LED_COUNT; i++) {
            buf[i * 2 + 0] = pair[0];
            buf[i * 2 + 1] = pair[1];
        }
        if (io_expander_->SetLedData(buf, sizeof(buf))) {
            io_expander_->RefreshLeds();
        }
    }

    void InitializeServo() {
        ESP_LOGI(TAG, "Init SCS0009 servo bus (UART%d, baud=%d, tx=%d, rx=%d)",
                 SERVO_UART_NUM, SERVO_BAUDRATE, SERVO_TX_PIN, SERVO_RX_PIN);
        // FeetechScs::begin() returns void and uses ESP_ERROR_CHECK internally,
        // so a UART configuration error aborts the boot rather than reporting
        // false. If begin() returns to us, init succeeded.
        scs_bus_.begin(SERVO_UART_NUM, SERVO_BAUDRATE, SERVO_TX_PIN, SERVO_RX_PIN);
        servo_ok_ = true;
        // ACK reading is enabled (SCS::Level defaults to 1). genWrite() will
        // wait for the SCS0009's 6-byte ACK packet before returning, which
        // implicitly enforces an inter-frame gap and prevents a follow-up
        // WritePos from colliding with a still-processing servo. This aligns
        // with the M5 StackChan official BSP behaviour (which never touches
        // Level). Was: scs_bus_.Level = 0 — turned out to silently drop
        // every WritePos after the first one ("starts moving once, then
        // never again" symptom).
        ESP_LOGI(TAG, "Servo bus init: %s (Level=1, ACK enabled)", servo_ok_ ? "OK" : "FAILED");

        if (servo_ok_) {
            motion_mutex_ = xSemaphoreCreateMutex();
            scs_bus_mutex_ = xSemaphoreCreateMutex();
            if (motion_mutex_ == nullptr || scs_bus_mutex_ == nullptr) {
                ESP_LOGE(TAG, "Failed to create servo mutexes: motion=%p scs_bus=%p; disabling servo",
                         motion_mutex_, scs_bus_mutex_);
                if (motion_mutex_ != nullptr) {
                    vSemaphoreDelete(motion_mutex_);
                    motion_mutex_ = nullptr;
                }
                if (scs_bus_mutex_ != nullptr) {
                    vSemaphoreDelete(scs_bus_mutex_);
                    scs_bus_mutex_ = nullptr;
                }
                servo_ok_ = false;
                return;
            }

#if CONFIG_STACKCHAN_SERVO_DELEGATED_MOTION
            motion_driver_ = std::make_unique<ServoDelegatedMotionDriver>(
                scs_bus_, scs_bus_mutex_, motion_mutex_,
                yaw_motion_, pitch_motion_);
#else
            motion_driver_ = std::make_unique<HostInterpolationMotionDriver>(
                scs_bus_, scs_bus_mutex_, motion_mutex_,
                yaw_motion_, pitch_motion_);
#endif
            if (!motion_driver_->Initialize()) {
                ESP_LOGE(TAG, "Failed to initialize motion driver; disabling servo");
                motion_driver_.reset();
                vSemaphoreDelete(motion_mutex_);
                motion_mutex_ = nullptr;
                vSemaphoreDelete(scs_bus_mutex_);
                scs_bus_mutex_ = nullptr;
                servo_ok_ = false;
                return;
            }

            // Issue #121 (Problem 1, "downward drop on power-on") + #123
            // (boot-init diagnostics).
            //
            // Background: the SCS0009 retains its commanded set-point
            // across power cycles (Hypothesis 1 in #121, confirmed by
            // the firmware-v1.4.1 clean-install reproduction -- after a
            // full NVS reset on the ESP32 side, the boot pre-init
            // `ReadPos` still matched the pre-power-off pose exactly,
            // demonstrating the set-point lives in the servo itself,
            // not in firmware-side NVS). When VM_EN asserts at boot,
            // the servo restores torque and snaps toward that retained
            // target before any firmware-side speed limiting can apply
            // -- audible as a mechanical end-stop impact when the
            // previous session ended near pitch=0, and visible as a
            // downward drop in general.
            //
            // The mitigation is a `WritePos(id, current_pos, time=0,
            // speed=0)` per servo, which the SCS0009 treats as a new
            // target equal to its current position. This interrupts any
            // in-progress snap motion and leaves the servo stationary
            // at the raw position the immediately-preceding `ReadPos`
            // observed, until the subsequent interpolating boot-init
            // climb begins.
            //
            // Efficacy depends on the `ReadPos` + `WritePos` pair
            // completing while the servo is still mid-snap. To minimize
            // that window, pitch (the only axis that exhibits the
            // downward drop) is read AND held BEFORE the yaw axis is
            // touched on the SCS0009 bus -- any yaw `ReadPos` /
            // `WritePos` / ACK wait interposed between the pitch
            // `ReadPos` and pitch `WritePos` would widen the window
            // and risk the snap completing into an end-stop before the
            // pitch hold reaches the servo. The unified "Boot pre-init
            // ReadPos" diagnostic line (#123) is emitted after both
            // holds because it is purely informational and not on the
            // timing-critical path. If a `ReadPos` value lands close
            // to a previous session's commanded target (e.g. raw pitch
            // near 620 for pitch=0 deg), the snap was likely still in
            // progress and the hold is expected to truncate it; if a
            // `ReadPos` value is already at an end-stop or fails, the
            // hold for that boot is a no-op or skipped and a deeper
            // fix (e.g. firmware-controlled VM_EN sequencing through
            // the PY32 IO-expander) would be required, tracked
            // separately.

            // Phase 1a: pitch first -- read and immediately hold.
            //
            // Issue #138: retry ReadPos to absorb the SCS0009 ~200 ms
            // startup latency observed after VM_EN HIGH on the PMIC
            // long-press OFF/ON path. Without retry, the first ReadPos
            // (measured at around tick 140 on this hardware) typically
            // lands inside the wake-up window and returns -1, causing
            // the snap-suppress hold below to skip on exactly the path
            // #121 Problem 1 targets. Budget: 5 attempts × 50 ms = 250 ms
            // total per axis, well above the observed ~200 ms latency.
            // This is distinct from the SCS0009 bus hang (#100), which a
            // fixed retry budget cannot clear; in that case all attempts
            // fail and the safe-fallback branch in Phase 2 below seeds
            // pitch_motion_.current_deg with BOOT_INIT_PITCH_DEG to avoid
            // an end-stop walk during the subsequent boot-init
            // `WriteHeadAngles` interpolation.
            // Loop form mirrors the `get_head_angles` MCP-tool retry below
            // (set attempts to `i + 1` inside the loop so the final value
            // equals the number of attempts actually made, even when all
            // retries fail). The previous `for (attempts = 1; attempts <=
            // MAX; ++attempts)` form left attempts at MAX+1 on failure
            // and made the diagnostic log overstate the attempt count.
            constexpr int BOOT_READPOS_MAX_ATTEMPTS = 5;
            constexpr uint32_t BOOT_READPOS_RETRY_MS = 50;
            int pitch_pos_actual = -1;
            int pitch_attempts = 0;
            for (int i = 0; i < BOOT_READPOS_MAX_ATTEMPTS; ++i) {
                pitch_attempts = i + 1;
                pitch_pos_actual = scs_bus_.ReadPos(SERVO_PITCH_ID);
                if (pitch_pos_actual >= 0) break;
                if (i + 1 < BOOT_READPOS_MAX_ATTEMPTS) {
                    vTaskDelay(pdMS_TO_TICKS(BOOT_READPOS_RETRY_MS));
                }
            }
            if (pitch_pos_actual >= 0) {
                // Bound the snap-suppress hold to the SAFE_PITCH_MIN..
                // SAFE_PITCH_MAX range applied at every other pitch
                // servo-write boundary in this file (see PitchDegToPos
                // and the Phase 2 restored_pitch clamp below). If the
                // boot `ReadPos` lands outside that range -- for example
                // because the servo bus came back online holding a
                // previous session's out-of-range set-point, or the
                // head was hand-pushed beyond an end-stop -- writing
                // the raw position back would bypass that safety
                // boundary and pin the servo against the stall current
                // it is held there from. In that case skip the hold
                // and let the subsequent interpolating boot-init climb
                // to (yaw=0, pitch=45) drive the head back into the
                // safe range through the existing speed-limited path.
                constexpr int PITCH_SAFE_RAW_MIN =
                    620 + SAFE_PITCH_MIN * 16 / 5;  // raw 620 at deg=0
                constexpr int PITCH_SAFE_RAW_MAX =
                    620 + SAFE_PITCH_MAX * 16 / 5;  // raw 901 at deg=88
                if (pitch_pos_actual >= PITCH_SAFE_RAW_MIN &&
                    pitch_pos_actual <= PITCH_SAFE_RAW_MAX) {
                    int pitch_hold_r = scs_bus_.WritePos(
                        SERVO_PITCH_ID, pitch_pos_actual, 0, 0);
                    ESP_LOGI(TAG,
                             "Boot snap-suppress pitch hold(pos=%d): r=%d",
                             pitch_pos_actual, pitch_hold_r);
                } else {
                    ESP_LOGW(TAG,
                             "Boot snap-suppress pitch skipped: ReadPos=%d outside safe raw range [%d, %d]; relying on boot-init climb",
                             pitch_pos_actual,
                             PITCH_SAFE_RAW_MIN, PITCH_SAFE_RAW_MAX);
                }
            }

            // Phase 1b: yaw second -- no analogous snap-into-end-stop
            // failure mode, so timing is not critical. Retry budget
            // matches pitch (Issue #138) for symmetry; in practice yaw
            // typically succeeds on the first attempt because the pitch
            // retries above have already consumed the SCS0009 startup-
            // latency window on the shared bus.
            int yaw_pos_actual = -1;
            int yaw_attempts = 0;
            for (int i = 0; i < BOOT_READPOS_MAX_ATTEMPTS; ++i) {
                yaw_attempts = i + 1;
                yaw_pos_actual = scs_bus_.ReadPos(SERVO_YAW_ID);
                if (yaw_pos_actual >= 0) break;
                if (i + 1 < BOOT_READPOS_MAX_ATTEMPTS) {
                    vTaskDelay(pdMS_TO_TICKS(BOOT_READPOS_RETRY_MS));
                }
            }
            if (yaw_pos_actual >= 0) {
                int yaw_hold_r = scs_bus_.WritePos(
                    SERVO_YAW_ID, yaw_pos_actual, 0, 0);
                ESP_LOGI(TAG,
                         "Boot snap-suppress yaw hold(pos=%d): r=%d",
                         yaw_pos_actual, yaw_hold_r);
            }

            // Phase 1c (diagnostic, #123): unified pre-init ReadPos log
            // with tick timestamp. Off the timing-critical path
            // intentionally; ServoTask has not been created yet, so no
            // `scs_bus_mutex_` contention is possible at this point.
            ESP_LOGI(TAG,
                     "Boot pre-init ReadPos: yaw_raw=%d (attempts=%d) "
                     "pitch_raw=%d (attempts=%d) tick=%u",
                     yaw_pos_actual, yaw_attempts,
                     pitch_pos_actual, pitch_attempts,
                     (unsigned)xTaskGetTickCount());

            // Phase 2: software-side current_deg restore. Order does not
            // affect the SCS0009 bus -- these only update firmware-side
            // motion state for the upcoming interpolating boot-init
            // climb.
            if (yaw_pos_actual >= 0) {
                yaw_motion_.current_deg = (yaw_pos_actual - 460) * 5 / 16;
                ESP_LOGI(TAG, "Restored yaw_motion_.current_deg=%d from ReadPos=%d",
                         yaw_motion_.current_deg, yaw_pos_actual);
            } else {
                // Issue #138: seed yaw current_deg to BOOT_INIT_YAW_DEG
                // rather than leaving the struct default. The default
                // happens to be 0 (== BOOT_INIT_YAW_DEG today), so this
                // is a no-op assignment in current numerical terms; the
                // explicit form keeps intent visible and propagates any
                // future change to BOOT_INIT_YAW_DEG.
                yaw_motion_.current_deg = BOOT_INIT_YAW_DEG;
                ESP_LOGW(TAG,
                         "Failed to ReadPos(yaw) after %d attempts; "
                         "seeded current_deg=%d (BOOT_INIT_YAW_DEG)",
                         BOOT_READPOS_MAX_ATTEMPTS, BOOT_INIT_YAW_DEG);
            }
            if (pitch_pos_actual >= 0) {
                int restored_pitch = (pitch_pos_actual - 620) * 5 / 16;
                // Issue #80: if the device booted with the head physically
                // pushed below the safe range (e.g. previous unsafe firmware
                // or manual handling), don't carry that negative starting
                // angle into motion interpolation — clamp before storing so
                // subsequent interpolation runs only over safe positions.
                if (restored_pitch < SAFE_PITCH_MIN) restored_pitch = SAFE_PITCH_MIN;
                if (restored_pitch > SAFE_PITCH_MAX) restored_pitch = SAFE_PITCH_MAX;
                pitch_motion_.current_deg = restored_pitch;
                ESP_LOGI(TAG, "Restored pitch_motion_.current_deg=%d from ReadPos=%d (clamped to safe range %d..%d)",
                         pitch_motion_.current_deg, pitch_pos_actual, SAFE_PITCH_MIN, SAFE_PITCH_MAX);
            } else {
                // Issue #138: seed pitch current_deg to BOOT_INIT_PITCH_DEG.
                // Without this, the boot-init `WriteHeadAngles(0, 45, 4000)`
                // interpolation below would start from the struct-default
                // `current_deg=0` (== pos=620 at deg=0, the lower mechanical
                // end-stop) and walk WritePos calls upward (pos=620, 623,
                // 626, ...) through end-stop-adjacent positions before
                // reaching the target -- risking servo bus degradation if
                // the SCS0009 wakes up mid-sequence. Seeding to
                // BOOT_INIT_PITCH_DEG makes the subsequent interpolation a
                // near-no-op (start_deg == target_deg == 45) which keeps
                // the servo away from end-stop territory throughout the
                // wake-up window. This is the firmware-side counterpart to
                // the Phase 1a ReadPos retry: retry absorbs the typical
                // wake-up case so the snap-suppress hold can fire; this
                // seed handles the residual case where wake-up exceeds the
                // retry budget or the bus is genuinely hung (#100).
                pitch_motion_.current_deg = BOOT_INIT_PITCH_DEG;
                ESP_LOGW(TAG,
                         "Failed to ReadPos(pitch) after %d attempts; "
                         "seeded current_deg=%d (BOOT_INIT_PITCH_DEG) "
                         "to avoid end-stop walk during boot-init climb",
                         BOOT_READPOS_MAX_ATTEMPTS, BOOT_INIT_PITCH_DEG);
            }

            BaseType_t ok = xTaskCreate(&StackChanBoard::ServoTaskTrampoline,
                                        "servo_motion", 4096, this, 5,
                                        &servo_task_handle_);
            if (ok != pdPASS) {
                ESP_LOGE(TAG, "Failed to create servo_motion task; disabling servo");
                if (motion_mutex_ != nullptr) {
                    vSemaphoreDelete(motion_mutex_);
                    motion_mutex_ = nullptr;
                }
                if (scs_bus_mutex_ != nullptr) {
                    vSemaphoreDelete(scs_bus_mutex_);
                    scs_bus_mutex_ = nullptr;
                }
                motion_driver_.reset();
                servo_task_handle_ = nullptr;
                servo_ok_ = false;
                return;
            }

            // Issue #115: boot-time initialization to a fall-safe neutral
            // pose. Without this, the head retains whatever angle it was
            // left at on power-down — including end-stop positions (e.g.
            // pitch=0) that trigger the SCS0009 bus-hang documented in
            // #100 on the very first user-driven motion. By the time any
            // MCP command can arrive, we want the head already moved to
            // the center of the M5Stack-recommended 5..85° pitch range,
            // well clear of both mechanical end-stops.
            //
            // Design follows the goHome() pattern in m5stack/StackChan
            // (apps/app_setup/workers/servo.cpp:144) where the setup
            // app calls motion.goHome(speed) at boot, and the 1-second
            // positioning timing established in mongonta0716/stackchan-
            // arduino attachServos(). 1000ms move via the existing
            // interpolating WriteHeadAngles path keeps frame-rate stall
            // currents low; the extra 100ms vTaskDelay covers servo
            // settling before any subsequent motion can arrive.
            //
            // Implements #99 Option C and the boot-init aspect of #100
            // direction E. Existing pitch guards (#80 / #98 / #109)
            // continue to apply unchanged.
            // BOOT_INIT_YAW_DEG / BOOT_INIT_PITCH_DEG / BOOT_INIT_MOVE_MS
            // are class-level static constexpr; see the comment block at
            // their declaration above the YawDegToPos helper for the full
            // rationale (Issue #115 target pose, Issue #121 Problem 2
            // slower climb, Issue #138 promotion to class scope for the
            // safe-fallback seed in Phase 2 above).
            // Compute Phase 0 duration from the current_deg → target
            // deltas at BOOT_INIT_TARGET_DEG_PER_SEC, floored at
            // BOOT_INIT_MOVE_MS to keep the SCS0009 wake-up latency
            // window covered on the PMIC OFF/ON path (where Phase 0
            // is a no-op of effect but the BOOT_INIT_MOVE_MS budget
            // still needs to elapse before Phase 0' ReadPos). Without
            // this calculation, a yaw-90° (or any large pre-power-off
            // angle) prior set-point would run the boot-init yaw
            // motion at 30 deg/s+, exceeding the 15 deg/s cap.
            int phase0_yaw_delta;
            int phase0_pitch_delta;
            phase0_yaw_delta =
                BOOT_INIT_YAW_DEG - static_cast<int>(motion_driver_->GetYawDeg());
            phase0_pitch_delta =
                BOOT_INIT_PITCH_DEG - static_cast<int>(motion_driver_->GetPitchDeg());
            if (phase0_yaw_delta < 0) phase0_yaw_delta = -phase0_yaw_delta;
            if (phase0_pitch_delta < 0) phase0_pitch_delta = -phase0_pitch_delta;
            int phase0_max_delta = phase0_yaw_delta > phase0_pitch_delta
                ? phase0_yaw_delta : phase0_pitch_delta;
            uint32_t phase0_duration_ms =
                (uint32_t)phase0_max_delta * 1000U /
                BOOT_INIT_TARGET_DEG_PER_SEC;
            if (phase0_duration_ms < BOOT_INIT_MOVE_MS) {
                phase0_duration_ms = BOOT_INIT_MOVE_MS;
            }
            TickType_t boot_init_start_tick = xTaskGetTickCount();
            WriteHeadAngles(BOOT_INIT_YAW_DEG, BOOT_INIT_PITCH_DEG,
                            phase0_duration_ms,
                            /* prefer_linear = */ true);
            // Two-phase boot-init wait:
            //
            // (1) Mandatory minimum: vTaskDelay until
            //     phase0_duration_ms + 100 ms has elapsed. This must
            //     run UNCONDITIONALLY — independent of IsMoving() —
            //     because phase0_duration_ms is floored to
            //     BOOT_INIT_MOVE_MS specifically to cover the
            //     SCS0009 wake-up window on the PMIC OFF/ON path,
            //     even when WriteHeadAngles is a no-op (Phase 2
            //     safe-fallback seeded current_deg to exactly the
            //     boot target → motion_driver_->IsMoving() == false
            //     immediately → Phase 0' ReadPos would otherwise run
            //     inside the wake-up latency window and exhaust).
            //
            // (2) Optional extension: while motion_driver_->IsMoving()
            //     reports a motion still in flight, keep waiting up
            //     to a safety deadline. This covers the ServoDelegated
            //     path where the actual servo motion can start late
            //     due to dispatch retry latency (max 5 ×
            //     MOTION_POLL_INTERVAL_MS = 250 ms) — phase (1)'s
            //     budget may finish before the servo has completed
            //     its internal interpolation in that worst case.
            TickType_t boot_init_min_deadline =
                xTaskGetTickCount() +
                pdMS_TO_TICKS(phase0_duration_ms + 100);
            // Safety extension covers the worst-case ServoDelegated
            // dispatch latency. Each retry round dispatches yaw and
            // pitch sequentially under scs_bus_mutex_, so one fully-
            // timing-out round on both axes costs approximately:
            //   MOTION_POLL_INTERVAL_MS (50 ms tick wake)
            // + yaw WritePos ACK timeout (~100 ms; servo bus
            //   SCSerial::IOTimeOut = 100 ms, FeetechScs comparable)
            // + kInterFrameGap (10 ms)
            // + pitch WritePos ACK timeout (~100 ms)
            // ≈ 260 ms per retry round.
            //
            // 4 failing rounds + 1 successful round bound the
            // worst-case dispatch latency at roughly 4 × 260 ≈ 1040 ms.
            // Add a settle margin so the wait outlasts a genuine
            // delayed dispatch instead of breaking while IsMoving()
            // is legitimately true. 2000 ms covers the full retry
            // budget plus margin.
            //
            // HostInterpolation path is unaffected (dispatch latency
            // is 0 there; the wait exits well before this deadline
            // regardless).
            TickType_t boot_init_safety_deadline =
                boot_init_min_deadline + pdMS_TO_TICKS(2000);
            while ((int32_t)(xTaskGetTickCount() -
                             boot_init_min_deadline) < 0) {
                vTaskDelay(pdMS_TO_TICKS(50));
            }
            while (motion_driver_->IsMoving()) {
                if ((int32_t)(xTaskGetTickCount() -
                              boot_init_safety_deadline) >= 0) {
                    ESP_LOGW(TAG,
                             "Boot init Phase 0 wait safety deadline elapsed while motion_driver_ still reports moving; proceeding to Phase 0' ReadPos anyway");
                    break;
                }
                vTaskDelay(pdMS_TO_TICKS(50));
            }

            // Issue #123: capture post-init ReadPos so the boot-init effect
            // is observable in the serial log. ServoTask is now running, so
            // hold scs_bus_mutex_ across the ReadPos pair.
            //
            // Retry budget mirrors Phase 1a / 1b and the get_head_angles
            // MCP-tool retry. A transient `ReadPos == -1` on a healthy
            // servo would otherwise cause Phase 0' re-sync to skip,
            // leaving current_deg slightly stale for the session.
            int post_yaw_pos = -1;
            int post_pitch_pos = -1;
            int post_yaw_attempts = 0;
            int post_pitch_attempts = 0;
            xSemaphoreTake(scs_bus_mutex_, portMAX_DELAY);
            for (int i = 0; i < BOOT_READPOS_MAX_ATTEMPTS; ++i) {
                post_yaw_attempts = i + 1;
                post_yaw_pos = scs_bus_.ReadPos(SERVO_YAW_ID);
                if (post_yaw_pos >= 0) break;
                if (i + 1 < BOOT_READPOS_MAX_ATTEMPTS) {
                    vTaskDelay(pdMS_TO_TICKS(BOOT_READPOS_RETRY_MS));
                }
            }
            for (int i = 0; i < BOOT_READPOS_MAX_ATTEMPTS; ++i) {
                post_pitch_attempts = i + 1;
                post_pitch_pos = scs_bus_.ReadPos(SERVO_PITCH_ID);
                if (post_pitch_pos >= 0) break;
                if (i + 1 < BOOT_READPOS_MAX_ATTEMPTS) {
                    vTaskDelay(pdMS_TO_TICKS(BOOT_READPOS_RETRY_MS));
                }
            }
            xSemaphoreGive(scs_bus_mutex_);
            TickType_t boot_init_end_tick = xTaskGetTickCount();
            ESP_LOGI(TAG,
                     "Boot-time servo init complete: target yaw=%d pitch=%d "
                     "(move=%ums), post-ReadPos: yaw_raw=%d (attempts=%d) "
                     "pitch_raw=%d (attempts=%d), elapsed_ms=%u",
                     BOOT_INIT_YAW_DEG, BOOT_INIT_PITCH_DEG,
                     (unsigned)phase0_duration_ms,
                     post_yaw_pos, post_yaw_attempts,
                     post_pitch_pos, post_pitch_attempts,
                     (unsigned)((boot_init_end_tick - boot_init_start_tick) *
                                portTICK_PERIOD_MS));

            // Phase 0': mandatory current_deg re-sync from post-init
            // ReadPos before boot-time servo initialization completes.
            //
            // Background: on the PMIC long-press OFF / ON path, Phase 1
            // ReadPos retries can exhaust the budget while the SCS0009
            // is still in its wake-up latency window; Phase 2 then
            // seeds current_deg with BOOT_INIT_*_DEG so the Phase 0
            // interpolation is a no-op of effect. By the time the
            // BOOT_INIT_MOVE_MS-long vTaskDelay above has elapsed,
            // the SCS0009 has been powered for several seconds and a
            // ReadPos here is almost certain to succeed (the
            // observed boot log shows yaw_raw / pitch_raw populated
            // at tick ≥ ~6 s).
            //
            // Re-syncing current_deg with the actual physical position
            // now keeps the next move_head interpolation anchored to
            // where the servo really is, rather than to the Phase 2
            // safe-fallback seed.
            //
            // If a post-init ReadPos still fails, leave current_deg as
            // restored-or-seeded by Phase 2. That is safer than issuing
            // additional boot-time WritePos commands on a degraded bus.
            if (post_yaw_pos >= 0) {
                int actual_yaw_deg = (post_yaw_pos - 460) * 5 / 16;
                xSemaphoreTake(motion_mutex_, portMAX_DELAY);
                if (yaw_motion_.current_deg != actual_yaw_deg) {
                    int prev_yaw_deg = yaw_motion_.current_deg;
                    yaw_motion_.current_deg = actual_yaw_deg;
                    yaw_motion_.start_deg = actual_yaw_deg;
                    yaw_motion_.target_deg = actual_yaw_deg;
                    yaw_motion_.moving = false;
                    yaw_motion_.move_start_ms =
                        (uint32_t)(boot_init_end_tick * portTICK_PERIOD_MS);
                    // Bump the driver's request_token so any Tick()
                    // snapshot taken before this re-sync no longer
                    // passes the post-bus freshness guard and does
                    // not overwrite the just-re-synced state. The
                    // delegated driver also clears its per-axis
                    // private cancellation state under the same
                    // motion_mutex_ hold.
                    motion_driver_->InvalidateAxisToken(SERVO_YAW_ID);
                    xSemaphoreGive(motion_mutex_);
                    ESP_LOGI(TAG,
                             "Phase 0' yaw re-sync: current_deg %d -> %d "
                             "(actual ReadPos=%d)",
                             prev_yaw_deg, actual_yaw_deg, post_yaw_pos);
                } else {
                    xSemaphoreGive(motion_mutex_);
                }
            } else {
                // Phase 0' ReadPos failed: current_deg holds the
                // Phase 2 restored-or-seeded value, which has NOT
                // been physically verified. Mark the axis position
                // unknown so the ServoDelegated path's no-op gate
                // does not silently skip a same-target recovery
                // command. The HostInterpolation path ignores this
                // flag; on that path the next WriteHeadAngles still
                // dispatches a fresh interpolation as before.
                xSemaphoreTake(motion_mutex_, portMAX_DELAY);
                yaw_motion_.position_unknown = true;
                // Token/state invalidation covers the position_unknown
                // mutation using the same external-reset boundary as
                // the success branch above.
                motion_driver_->InvalidateAxisToken(SERVO_YAW_ID);
                xSemaphoreGive(motion_mutex_);
                ESP_LOGW(TAG,
                         "Phase 0' yaw re-sync skipped: ReadPos failed; "
                         "leaving current_deg at restored-or-seeded value "
                         "and marking position unknown for delegated no-op gate");
            }
            if (post_pitch_pos >= 0) {
                int actual_pitch_deg = (post_pitch_pos - 620) * 5 / 16;
                if (actual_pitch_deg < SAFE_PITCH_MIN) actual_pitch_deg = SAFE_PITCH_MIN;
                if (actual_pitch_deg > SAFE_PITCH_MAX) actual_pitch_deg = SAFE_PITCH_MAX;
                xSemaphoreTake(motion_mutex_, portMAX_DELAY);
                if (pitch_motion_.current_deg != actual_pitch_deg) {
                    int prev_pitch_deg = pitch_motion_.current_deg;
                    pitch_motion_.current_deg = actual_pitch_deg;
                    pitch_motion_.start_deg = actual_pitch_deg;
                    pitch_motion_.target_deg = actual_pitch_deg;
                    pitch_motion_.moving = false;
                    pitch_motion_.move_start_ms =
                        (uint32_t)(boot_init_end_tick * portTICK_PERIOD_MS);
                    // Invalidate the driver's token/state boundary
                    // (see the yaw branch above for the rationale).
                    motion_driver_->InvalidateAxisToken(SERVO_PITCH_ID);
                    xSemaphoreGive(motion_mutex_);
                    ESP_LOGI(TAG,
                             "Phase 0' pitch re-sync: current_deg %d -> %d "
                             "(actual ReadPos=%d, clamped to safe range %d..%d)",
                             prev_pitch_deg, actual_pitch_deg, post_pitch_pos,
                             SAFE_PITCH_MIN, SAFE_PITCH_MAX);
                } else {
                    xSemaphoreGive(motion_mutex_);
                }
            } else {
                xSemaphoreTake(motion_mutex_, portMAX_DELAY);
                pitch_motion_.position_unknown = true;
                // Invalidate the driver's token/state boundary for the
                // position_unknown mutation (see the yaw fail branch
                // above).
                motion_driver_->InvalidateAxisToken(SERVO_PITCH_ID);
                xSemaphoreGive(motion_mutex_);
                ESP_LOGW(TAG,
                         "Phase 0' pitch re-sync skipped: ReadPos failed; "
                         "leaving current_deg at restored-or-seeded value "
                         "and marking position unknown for delegated no-op gate");
            }
            boot_init_done_.store(true, std::memory_order_release);
        }
    }

    ServoTorqueResult InternalSetServoTorque(bool yaw_enabled,
                                             bool pitch_enabled,
                                             ReleaseReason reason,
                                             uint32_t expected_release_epoch =
                                                 0) {
        ServoTorqueResult result;
        const bool disables_axis = !yaw_enabled || !pitch_enabled;

        auto update_bus_ok = [&]() {
            result.yaw_ok = (result.yaw_bus_return == 0);
            result.pitch_ok = (result.pitch_bus_return == 0);
        };

        // Issue #171: classify each exit path with a single 3-valued tag so
        // that idempotent_short_circuit and wait_exhausted can never both be
        // set. A one-bool flag could not express the two orthogonal outcomes;
        // a single enum makes "both true" structurally unrepresentable.
        //   * kBusAction:     a real EnableTorque() bus write was attempted
        //                     (or the servo subsystem was unavailable); the
        //                     outcome is carried by yaw_ok/pitch_ok. Neither
        //                     short-circuit flag is set.
        //   * kIdempotent:    returned without a bus frame, state already
        //                     matched the request (success no-op).
        //   * kWaitExhausted: returned without a bus frame, the kReleasing
        //                     wait budget was exhausted (failure).
        enum class ExitKind { kBusAction, kIdempotent, kWaitExhausted };

        auto log_result = [&](ExitKind kind) {
            result.idempotent_short_circuit = (kind == ExitKind::kIdempotent);
            result.wait_exhausted = (kind == ExitKind::kWaitExhausted);
            // Defensive: the enum makes this impossible, but assert anyway so
            // any future direct field mutation is caught in debug builds.
            assert(!(result.idempotent_short_circuit && result.wait_exhausted));
            ESP_LOGI(TAG,
                     "set_servo_torque (reason=%s): servo_ok=%d "
                     "yaw_enabled=%d (r=%d) pitch_enabled=%d (r=%d) "
                     "idempotent_short_circuit=%d wait_exhausted=%d",
                     ReleaseReasonName(reason),
                     servo_ok_ ? 1 : 0,
                     yaw_enabled ? 1 : 0, result.yaw_bus_return,
                     pitch_enabled ? 1 : 0, result.pitch_bus_return,
                     result.idempotent_short_circuit ? 1 : 0,
                     result.wait_exhausted ? 1 : 0);
        };

        auto finish = [&](ExitKind kind) -> ServoTorqueResult {
            log_result(kind);
            return result;
        };

        auto publish_after_bus_attempt = [&](TorqueState pre_bus_state) {
            const bool any_axis_bus_failed =
                !result.yaw_ok || !result.pitch_ok;
            if (!any_axis_bus_failed) {
                // Success-path cached-state ownership is pre-existing and
                // tracked separately under Issue #172.
                PublishTorqueState();
                return;
            }
            TorqueState expected = pre_bus_state;
            if (torque_state_.compare_exchange_strong(
                    expected,
                    TorqueState::kUncertain,
                    std::memory_order_release,
                    std::memory_order_acquire)) {
                ESP_LOGW(TAG,
                         "set_servo_torque (reason=%s): publishing kUncertain; "
                         "bus confirmation failed for yaw_failed=%d (r=%d) "
                         "pitch_failed=%d (r=%d)",
                         ReleaseReasonName(reason),
                         result.yaw_ok ? 0 : 1, result.yaw_bus_return,
                         result.pitch_ok ? 0 : 1, result.pitch_bus_return);
            } else {
                ESP_LOGW(TAG,
                         "set_servo_torque (reason=%s): kUncertain publish "
                         "skipped; torque_state_ advanced from %d to %d "
                         "(likely MarkReleasing()); leaving concurrent state "
                         "intact. bus_return yaw=%d pitch=%d",
                         ReleaseReasonName(reason),
                         static_cast<int>(pre_bus_state),
                         static_cast<int>(expected),
                         result.yaw_bus_return, result.pitch_bus_return);
            }
        };

        if (!servo_ok_ || scs_bus_mutex_ == nullptr) {
            // Servo subsystem unavailable: not a short-circuit and not a
            // wait timeout. yaw_ok/pitch_ok stay false, so ok is false.
            return finish(ExitKind::kBusAction);
        }

        // Fully-symmetric re-engage remains bus-ordered even when it becomes
        // a no-op. If an auto-idle OFF has already published kReleasing, the
        // manual path waits for that pending transition before entering the
        // bus section.
        if (yaw_enabled && pitch_enabled) {
            // Pre-mutex wait: this reduces obvious contention before
            // attempting scs_bus_mutex_. The post-mutex re-check below closes
            // the pre-check-to-mutex TOCTOU window.
            if (reason == ReleaseReason::kManual) {
                auto state_pre =
                    torque_state_.load(std::memory_order_acquire);
                if (state_pre == TorqueState::kReleasing) {
                    if (!WaitForKReleasingToClear()) {
                        ESP_LOGW(TAG,
                                 "set_servo_torque (reason=%s): kReleasing "
                                 "not clearing within wait budget; skipping "
                                 "bus frames, caller may retry.",
                                 ReleaseReasonName(reason));
                        // Pre-mutex wait budget exhausted: no bus frame went
                        // out, the requested ON did not happen (Issue #171).
                        log_result(ExitKind::kWaitExhausted);
                        return result;
                    }
                    // After the wait, state may be kEngaged (OFF rolled
                    // back), kReleased (OFF succeeded), or kUncertain (OFF
                    // bus confirmation failed). Fall through to the existing
                    // (true, true) logic, which short-circuits on kEngaged and
                    // proceeds normally on kReleased/kPartial/kUncertain.
                }
            }

            // Bounded retry for the remaining TOCTOU window: torque_state_
            // can flip to kReleasing between the pre-mutex check and
            // xSemaphoreTake(). Once the bus mutex is held, re-check; if an
            // auto-release OFF was published in that gap, release the mutex,
            // wait, and retry. kReengagement is exempt because
            // EnsureTorqueEngagedBeforeMove() already waited; kAutoIdle does
            // not enter this (true, true) branch.
            for (int attempt = 0; attempt < kMaxManualReengageRetries;
                 ++attempt) {
                xSemaphoreTake(scs_bus_mutex_, portMAX_DELAY);

                if (reason == ReleaseReason::kManual &&
                    torque_state_.load(std::memory_order_acquire) ==
                        TorqueState::kReleasing) {
                    xSemaphoreGive(scs_bus_mutex_);
                    if (!WaitForKReleasingToClear()) {
                        ESP_LOGW(TAG,
                                 "set_servo_torque (reason=%s): kReleasing "
                                 "persists after attempt %d; skipping bus "
                                 "frames, caller may retry.",
                                 ReleaseReasonName(reason),
                                 attempt);
                        // Post-mutex re-check wait budget exhausted: no bus
                        // frame, requested ON did not happen (Issue #171).
                        log_result(ExitKind::kWaitExhausted);
                        return result;
                    }
                    continue;
                }

                if (torque_state_.load(std::memory_order_acquire) ==
                    TorqueState::kEngaged) {
                    // Already engaged (reached here only after any kReleasing
                    // wait already cleared): legitimate no-op success, so ok
                    // stays true (Issue #171).
                    log_result(ExitKind::kIdempotent);
                    xSemaphoreGive(scs_bus_mutex_);
                    return result;
                }
                const TorqueState pre_bus_state =
                    torque_state_.load(std::memory_order_acquire);
                result.yaw_bus_return =
                    scs_bus_.EnableTorque(SERVO_YAW_ID, 1);
                result.pitch_bus_return =
                    scs_bus_.EnableTorque(SERVO_PITCH_ID, 1);
                update_bus_ok();
                if (result.yaw_ok) {
                    yaw_torque_enabled_ = true;
                }
                if (result.pitch_ok) {
                    pitch_torque_enabled_ = true;
                }
                publish_after_bus_attempt(pre_bus_state);
                // Real bus write attempted; ok is governed by yaw_ok/pitch_ok.
                log_result(ExitKind::kBusAction);
                xSemaphoreGive(scs_bus_mutex_);
                return result;
            }

            ESP_LOGW(TAG,
                     "set_servo_torque (reason=%s): kReleasing observed in "
                     "all %d post-mutex retries; skipping bus frames.",
                     ReleaseReasonName(reason),
                     kMaxManualReengageRetries);
            // All retries exhausted while still kReleasing: no bus frame, the
            // requested ON did not happen (Issue #171).
            log_result(ExitKind::kWaitExhausted);
            return result;
        } else {
            if (!yaw_enabled && !pitch_enabled) {
                xSemaphoreTake(scs_bus_mutex_, portMAX_DELAY);
                bool already_released =
                    torque_state_.load(std::memory_order_acquire) ==
                    TorqueState::kReleased;
                if (already_released) {
                    // Already released for an OFF request: legitimate no-op
                    // success, so ok stays true (Issue #171).
                    log_result(ExitKind::kIdempotent);
                    xSemaphoreGive(scs_bus_mutex_);
                    return result;
                }
                xSemaphoreGive(scs_bus_mutex_);
            }

            // Preserve the existing cancellation-first disable path exactly:
            // reset MotionDriver state for axes being disabled before the
            // EnableTorque(OFF) bus frames can race with ServoTask writes.
            if (motion_driver_ != nullptr && motion_mutex_ != nullptr &&
                disables_axis) {
                xSemaphoreTake(motion_mutex_, portMAX_DELAY);
                if (reason == ReleaseReason::kAutoIdle &&
                    (yaw_motion_.moving || pitch_motion_.moving)) {
                    // Auto-idle deferring because motion is still in progress:
                    // a benign no-op (no wait budget consumed), not a timeout.
                    xSemaphoreGive(motion_mutex_);
                    return finish(ExitKind::kIdempotent);
                }
                if (!yaw_enabled) {
                    yaw_motion_.moving = false;
                    yaw_motion_.position_unknown = true;
                    motion_driver_->InvalidateAxisToken(SERVO_YAW_ID);
                }
                if (!pitch_enabled) {
                    pitch_motion_.moving = false;
                    pitch_motion_.position_unknown = true;
                    motion_driver_->InvalidateAxisToken(SERVO_PITCH_ID);
                }
                // Mark the fully-OFF transition before releasing
                // motion_mutex_ so concurrent motion entries do not observe
                // the old engaged state while the OFF bus write is pending.
                if (!yaw_enabled && !pitch_enabled) {
                    (void)MarkReleasing();
                }
                xSemaphoreGive(motion_mutex_);
            }

            xSemaphoreTake(scs_bus_mutex_, portMAX_DELAY);
            if (reason == ReleaseReason::kAutoIdle &&
                expected_release_epoch != 0) {
                uint32_t current_epoch =
                    torque_release_epoch_.load(std::memory_order_acquire);
                auto current_state =
                    torque_state_.load(std::memory_order_acquire);
                if (current_epoch != expected_release_epoch ||
                    current_state != TorqueState::kReleasing) {
                    ESP_LOGW(TAG,
                             "auto-release OFF aborted at bus check: epoch=%u "
                             "(expected %u), state=%d (expected kReleasing); "
                             "skipping EnableTorque(0,0) frames.",
                             (unsigned)current_epoch,
                             (unsigned)expected_release_epoch,
                             (int)current_state);
                    xSemaphoreGive(scs_bus_mutex_);
                    // Stale auto-release OFF superseded by a newer epoch/state:
                    // the OFF is already obsolete, a benign no-op (Issue #171).
                    log_result(ExitKind::kIdempotent);
                    return result;
                }
            }
            const TorqueState pre_bus_state =
                torque_state_.load(std::memory_order_acquire);
            result.yaw_bus_return = scs_bus_.EnableTorque(
                SERVO_YAW_ID, yaw_enabled ? 1 : 0);
            result.pitch_bus_return = scs_bus_.EnableTorque(
                SERVO_PITCH_ID, pitch_enabled ? 1 : 0);
            update_bus_ok();
            if (result.yaw_ok) {
                yaw_torque_enabled_ = yaw_enabled;
            }
            if (result.pitch_ok) {
                pitch_torque_enabled_ = pitch_enabled;
            }
            publish_after_bus_attempt(pre_bus_state);
            // Real bus write attempted; ok is governed by yaw_ok/pitch_ok.
            log_result(ExitKind::kBusAction);
            xSemaphoreGive(scs_bus_mutex_);
            return result;
        }
    }

    void EnsureTorqueEngagedBeforeMove() {
        auto state = torque_state_.load(std::memory_order_acquire);
        if (state == TorqueState::kEngaged) {
            return;
        }
        if (!servo_ok_) {
            return;
        }

        if (state == TorqueState::kReleasing) {
            if (!WaitForKReleasingToClear()) {
                ESP_LOGW(TAG,
                         "EnsureTorqueEngagedBeforeMove: kReleasing not "
                         "clearing within wait budget, deferring to caller "
                         "retry.");
                return;
            }
            state = torque_state_.load(std::memory_order_acquire);
            if (state == TorqueState::kEngaged) {
                return;  // OFF rolled back to kEngaged
            }
        }

        // state is kPartial, kReleased, or kUncertain -- safe to
        // re-engage now.
        InternalSetServoTorque(true, true, ReleaseReason::kReengagement);
    }

    bool TakeMotionMutexAfterTorqueEngaged() {
        for (int attempt = 0; attempt < kMaxReengageRetries; ++attempt) {
            EnsureTorqueEngagedBeforeMove();
            xSemaphoreTake(motion_mutex_, portMAX_DELAY);
            if (torque_state_.load(std::memory_order_acquire) ==
                TorqueState::kEngaged) {
                return true;
            }
            xSemaphoreGive(motion_mutex_);
            vTaskDelay(pdMS_TO_TICKS(MOTION_TICK_MS));
        }
        ESP_LOGW(TAG,
                 "EnsureTorqueEngagedBeforeMove: re-engagement failed after "
                 "%d attempts; skipping motion entry to avoid silent "
                 "torque-off WritePos.",
                 kMaxReengageRetries);
        return false;
    }

    void MaybeAutoReleaseTorque() {
        if (!auto_release_enabled_.load(std::memory_order_acquire)) {
            return;
        }
        if (!servo_ok_) {
            return;
        }
        if (!boot_init_done_.load(std::memory_order_acquire)) {
            return;
        }
        if (physical_behavior_owner_.load(std::memory_order_acquire) !=
                PhysicalBehaviorOwner::IDLE) {
            last_motion_end_valid_ = false;
            return;
        }
        // PublishTorqueState() raises this when torque re-engages between
        // ServoTask ticks, so a stale idle timer cannot immediately re-OFF.
        if (idle_timer_reset_pending_.exchange(
                false, std::memory_order_acq_rel)) {
            last_motion_end_valid_ = false;
        }
        // Keep the idle window scoped to the currently engaged interval.
        // Released/partial/releasing states must not age a stale timer
        // into the next re-engage. kUncertain is treated as engaged for
        // auto-release purposes: if the kAutoIdle OFF bus frame was lost
        // on the UART path, the auto-release retry must continue so the
        // device does not strand with torque physically ON; if the OFF
        // was actually delivered, the next retry short-circuits via the
        // idempotent path (Issue #170 follow-up).
        auto current_state =
            torque_state_.load(std::memory_order_acquire);
        if (current_state != TorqueState::kEngaged &&
            current_state != TorqueState::kUncertain) {
            last_motion_end_valid_ = false;
            return;
        }
        bool moving = motion_driver_->IsMoving();
        uint32_t now_ms =
            static_cast<uint32_t>(esp_timer_get_time() / 1000);

        if (moving) {
            last_motion_end_valid_ = false;
            return;
        }

        if (!last_motion_end_valid_) {
            last_motion_end_ms_ = now_ms;
            last_motion_end_valid_ = true;
            return;
        }

        uint32_t idle_ms = now_ms - last_motion_end_ms_;
        uint32_t timeout_ms =
            auto_release_timeout_ms_.load(std::memory_order_acquire);
        if (idle_ms >= timeout_ms) {
            // Publish the pending OFF before InternalSetServoTorque() can
            // block on motion_mutex_, so a concurrent manual ON is routed
            // through the kReleasing wait/retry path instead of treating the
            // already-expired engaged state as a successful no-op.
            uint32_t my_pre_epoch = MarkReleasing();
            ServoTorqueResult r = InternalSetServoTorque(
                false, false, ReleaseReason::kAutoIdle,
                /*expected_release_epoch=*/my_pre_epoch + 1);
            auto state_after =
                torque_state_.load(std::memory_order_acquire);
            if (state_after == TorqueState::kReleased) {
                last_motion_end_valid_ = false;
            } else if (r.idempotent_short_circuit || r.wait_exhausted) {
                // Either short-circuit flag means the OFF returned without a
                // completed bus frame (Issue #171 split the old
                // short_circuited flag; for this kAutoIdle path only the
                // idempotent flag can fire, but the OR keeps the "no bus
                // action" intent explicit and future-proof).
                uint32_t current_epoch =
                    torque_release_epoch_.load(std::memory_order_acquire);
                if (current_epoch == my_pre_epoch) {
                    // Auto-idle re-observed motion under
                    // motion_mutex_ and returned before any bus frame went
                    // out, so the per-axis torque state is still fully
                    // engaged.
                    torque_state_.store(TorqueState::kEngaged,
                                        std::memory_order_release);
                    ESP_LOGW(TAG,
                             "auto-release OFF aborted by motion "
                             "re-check; rolled back torque_state_ to "
                             "kEngaged (epoch=%u), retry after "
                             "one idle window.",
                             (unsigned)my_pre_epoch);
                } else if (current_epoch == my_pre_epoch + 1) {
                    ESP_LOGW(TAG,
                             "auto-release OFF aborted at bus check; leaving "
                             "torque_state_ for concurrent publisher "
                             "(my_pre_epoch=%u current_epoch=%u).",
                             (unsigned)my_pre_epoch,
                             (unsigned)current_epoch);
                } else {
                    ESP_LOGW(TAG,
                             "auto-release OFF: release epoch advanced past "
                             "ours (my_pre_epoch=%u current_epoch=%u); "
                             "leaving torque_state_ for concurrent publisher.",
                             (unsigned)my_pre_epoch,
                             (unsigned)current_epoch);
                }
                last_motion_end_ms_ = now_ms;
            } else {
                last_motion_end_ms_ = now_ms;
                ESP_LOGW(TAG,
                         "auto-release OFF bus write failed: yaw_ok=%d "
                         "(r=%d) pitch_ok=%d (r=%d). Retrying after one "
                         "idle window.",
                         r.yaw_ok ? 1 : 0, r.yaw_bus_return,
                         r.pitch_ok ? 1 : 0, r.pitch_bus_return);
            }
        }
    }

    // ---- Head motion shared by expressions and explicit control ----------
    bool WriteHeadAngles(int yaw_deg, int pitch_deg,
                         uint32_t duration_ms = MOTION_DEFAULT_DURATION_MS,
                         bool prefer_linear = false) {
        if (physical_motion_unavailable_.load(std::memory_order_acquire)) {
            ESP_LOGW(TAG, "WriteHeadAngles skipped: motion is faulted");
            return false;
        }
        if (!servo_ok_ || motion_driver_ == nullptr) {
            ESP_LOGW(TAG, "WriteHeadAngles skipped: servo not initialized");
            return false;
        }
        if (!TakeMotionMutexAfterTorqueEngaged()) {
            return false;
        }
        motion_driver_->StartMove(yaw_deg, pitch_deg, duration_ms,
                                  prefer_linear);
        xSemaphoreGive(motion_mutex_);
        return true;
    }

    bool WriteHeadAngles(int yaw_deg, int pitch_deg, int speed_dps) {
        if (physical_motion_unavailable_.load(std::memory_order_acquire)) {
            ESP_LOGW(TAG, "WriteHeadAngles skipped: motion is faulted");
            return false;
        }
        if (!servo_ok_ || motion_driver_ == nullptr) {
            ESP_LOGW(TAG, "WriteHeadAngles(speed_dps) skipped: servo not initialized");
            return false;
        }
        int safe_speed = speed_dps;
        if (safe_speed <= 0) {
            safe_speed = DEFAULT_SPEED_DPS;
        } else if (safe_speed < MIN_STEP_SAFE_SPEED_DPS) {
            ESP_LOGW(TAG, "WriteHeadAngles: speed_dps=%d below MIN_STEP_SAFE_SPEED_DPS=%d, clamping",
                     speed_dps, MIN_STEP_SAFE_SPEED_DPS);
            safe_speed = MIN_STEP_SAFE_SPEED_DPS;
        } else if (safe_speed < MIN_SMOOTH_SPEED_DPS) {
            // Below the on-device measured smoothness floor -- motion will look
            // textured on SCS0009 at MOTION_TICK_MS=20 ms. This is intentionally
            // permitted (the gateway "low" preset is 30 dps, deliberately below
            // the floor for slow, expressive motion per Issue #129 design).
            // Log once so callers can see they are below the smooth zone.
            ESP_LOGW(TAG, "WriteHeadAngles: speed_dps=%d below MIN_SMOOTH_SPEED_DPS=%d (textured motion is expected)",
                     speed_dps, MIN_SMOOTH_SPEED_DPS);
        } else if (safe_speed > MAX_SPEED_DPS) {
            ESP_LOGW(TAG, "WriteHeadAngles: speed_dps=%d above MAX_SPEED_DPS=%d, clamping",
                     speed_dps, MAX_SPEED_DPS);
            safe_speed = MAX_SPEED_DPS;
        }

        int yaw_delta = std::abs(yaw_deg - static_cast<int>(motion_driver_->GetYawDeg()));
        int pitch_delta = std::abs(pitch_deg - static_cast<int>(motion_driver_->GetPitchDeg()));
        int max_delta = std::max(yaw_delta, pitch_delta);
        uint32_t duration_ms = std::max<uint32_t>(
            MOTION_TICK_MS,
            static_cast<uint32_t>(max_delta) * 1000U / static_cast<uint32_t>(safe_speed));
        return WriteHeadAngles(yaw_deg, pitch_deg, duration_ms);
    }

    bool WriteHeadCurve(const StackChanExpressionStep& curve) {
        if (physical_motion_unavailable_.load(std::memory_order_acquire) ||
            !servo_ok_ || motion_driver_ == nullptr ||
            !motion_driver_->SupportsCurve() ||
            !TakeMotionMutexAfterTorqueEngaged()) {
            return false;
        }
        bool started = motion_driver_->StartCurve(curve);
        xSemaphoreGive(motion_mutex_);
        return started;
    }

    bool PhysicalMotionInactive() {
        if (!servo_ok_ || motion_driver_ == nullptr) {
            return false;
        }
        xSemaphoreTake(motion_mutex_, portMAX_DELAY);
        bool inactive = !yaw_motion_.moving && !pitch_motion_.moving;
        xSemaphoreGive(motion_mutex_);
        return inactive;
    }

    bool PhysicalMotionCachedAt(int yaw_deg, int pitch_deg) {
        if (!servo_ok_ || motion_driver_ == nullptr) {
            return false;
        }
        xSemaphoreTake(motion_mutex_, portMAX_DELAY);
        bool matches = !yaw_motion_.moving && !pitch_motion_.moving &&
            !yaw_motion_.position_unknown &&
            !pitch_motion_.position_unknown &&
            yaw_motion_.current_deg == yaw_deg &&
            pitch_motion_.current_deg == pitch_deg;
        xSemaphoreGive(motion_mutex_);
        return matches;
    }

    bool SynchronizePhysicalMotionPosition(
            bool cancel_active_motion = false) {
        if (!servo_ok_ || motion_driver_ == nullptr ||
            motion_mutex_ == nullptr || scs_bus_mutex_ == nullptr) {
            return false;
        }
        xSemaphoreTake(motion_mutex_, portMAX_DELAY);
        if ((yaw_motion_.moving || pitch_motion_.moving) &&
            !cancel_active_motion) {
            xSemaphoreGive(motion_mutex_);
            return false;
        }
        if (cancel_active_motion) {
            yaw_motion_.moving = false;
            yaw_motion_.position_unknown = true;
            motion_driver_->InvalidateAxisToken(SERVO_YAW_ID);
            pitch_motion_.moving = false;
            pitch_motion_.position_unknown = true;
            motion_driver_->InvalidateAxisToken(SERVO_PITCH_ID);
        }
        xSemaphoreGive(motion_mutex_);

        int yaw_pos = -1;
        int pitch_pos = -1;
        xSemaphoreTake(scs_bus_mutex_, portMAX_DELAY);
        yaw_pos = scs_bus_.ReadPos(SERVO_YAW_ID);
        pitch_pos = scs_bus_.ReadPos(SERVO_PITCH_ID);
        xSemaphoreGive(scs_bus_mutex_);
        if (yaw_pos < 0 || pitch_pos < 0) {
            ESP_LOGW(
                TAG,
                "Motion resynchronization read failed: forced=%d "
                "yaw_raw=%d pitch_raw=%d torque_state=%d",
                cancel_active_motion ? 1 : 0,
                yaw_pos, pitch_pos,
                static_cast<int>(torque_state_.load(
                    std::memory_order_acquire)));
            return false;
        }

        int yaw_deg = (yaw_pos - 460) * 5 / 16;
        int pitch_deg = (pitch_pos - 620) * 5 / 16;
        pitch_deg = std::clamp(
            pitch_deg, SAFE_PITCH_MIN, SAFE_PITCH_MAX);
        uint32_t now_ms =
            static_cast<uint32_t>(esp_timer_get_time() / 1000);
        xSemaphoreTake(motion_mutex_, portMAX_DELAY);
        if (yaw_motion_.moving || pitch_motion_.moving) {
            xSemaphoreGive(motion_mutex_);
            return false;
        }
        yaw_motion_.current_deg = yaw_deg;
        yaw_motion_.start_deg = yaw_deg;
        yaw_motion_.target_deg = yaw_deg;
        yaw_motion_.move_start_ms = now_ms;
        yaw_motion_.position_unknown = false;
        motion_driver_->InvalidateAxisToken(SERVO_YAW_ID);
        pitch_motion_.current_deg = pitch_deg;
        pitch_motion_.start_deg = pitch_deg;
        pitch_motion_.target_deg = pitch_deg;
        pitch_motion_.move_start_ms = now_ms;
        pitch_motion_.position_unknown = false;
        motion_driver_->InvalidateAxisToken(SERVO_PITCH_ID);
        xSemaphoreGive(motion_mutex_);
        return true;
    }

    bool StartMeasuredIdleRecovery() {
        if (!SynchronizePhysicalMotionPosition(true)) {
            ESP_LOGW(
                TAG,
                "Idle recovery not commanded: pose measurement failed");
            return false;
        }
        return WriteHeadAngles(
            XC_BODY_IDLE_YAW_DEG,
            XC_BODY_IDLE_PITCH_DEG,
            XC_BODY_BEHAVIOR_SPEED_DPS);
    }

    bool PhysicalMotionConfirmedAt(
            int yaw_deg,
            int pitch_deg,
            const char* diagnostic_context = nullptr) {
        if (!servo_ok_ || motion_driver_ == nullptr) {
            if (diagnostic_context != nullptr) {
                ESP_LOGW(
                    TAG,
                    "Expression pose unavailable: context=%s servo_ok=%d",
                    diagnostic_context, servo_ok_ ? 1 : 0);
            }
            return false;
        }
        xSemaphoreTake(motion_mutex_, portMAX_DELAY);
        bool settled = !yaw_motion_.moving && !pitch_motion_.moving;
        xSemaphoreGive(motion_mutex_);
        int yaw_pos = -1;
        int pitch_pos = -1;
        if (settled) {
            xSemaphoreTake(scs_bus_mutex_, portMAX_DELAY);
            yaw_pos = scs_bus_.ReadPos(SERVO_YAW_ID);
            pitch_pos = scs_bus_.ReadPos(SERVO_PITCH_ID);
            xSemaphoreGive(scs_bus_mutex_);
        }
        constexpr int kPositionTolerance = 8;
        const int expected_yaw_pos = YawDegToPos(yaw_deg);
        const int expected_pitch_pos = PitchDegToPos(pitch_deg);
        const bool confirmed = settled && yaw_pos >= 0 && pitch_pos >= 0 &&
            std::abs(yaw_pos - expected_yaw_pos) <= kPositionTolerance &&
            std::abs(pitch_pos - expected_pitch_pos) <= kPositionTolerance;
        if (!confirmed && diagnostic_context != nullptr) {
            ESP_LOGW(
                TAG,
                "Expression pose check failed: context=%s settled=%d "
                "target_raw=(%d,%d) actual_raw=(%d,%d) torque_state=%d",
                diagnostic_context, settled ? 1 : 0,
                expected_yaw_pos, expected_pitch_pos, yaw_pos, pitch_pos,
                static_cast<int>(torque_state_.load(
                    std::memory_order_acquire)));
        }
        return confirmed;
    }

    bool ExpressionAdmissionIdle(bool preview) {
        return Application::GetInstance().GetDeviceState() ==
                kDeviceStateIdle &&
            !Application::GetInstance().AcceptsIncomingAudio() &&
            (!preview ||
             !offer_pending_.load(std::memory_order_acquire)) &&
            !settings_open_.load(std::memory_order_acquire) &&
            PhysicalMotionInactive();
    }

    bool ExpressionEnvironmentOwned(bool preview) {
        const DeviceState state =
            Application::GetInstance().GetDeviceState();
        const bool production = !preview;
        return (state == kDeviceStateIdle ||
                (production && state == kDeviceStateSpeaking)) &&
            (!Application::GetInstance().AcceptsIncomingAudio() ||
             production) &&
            (!preview ||
             !offer_pending_.load(std::memory_order_acquire)) &&
            !settings_open_.load(std::memory_order_acquire) &&
            PhysicalMotionInactive();
    }

    void StartXcBodyExpression(
            const std::string& behavior_id,
            const std::string& name,
            const char* success_subtype) {
        if (behavior_id.empty() || behavior_id.size() > 128) {
            throw std::invalid_argument(
                "behavior_id must contain 1 to 128 characters");
        }
        if (!IsStackChanExpressionName(name)) {
            throw std::invalid_argument("unknown expression");
        }
        StackChanExpressionRecipe recipe;
        if (LoadStackChanExpressionRecipe(name, recipe) !=
                StackChanExpressionLoadStatus::OK) {
            throw std::runtime_error(
                "expression is not calibrated");
        }
        const auto outcome = StartExpression(
            name,
            recipe,
            ExpressionInvocation::BEHAVIOR,
            behavior_id,
            success_subtype);
        if (outcome != StackChanExpressionOutcome::STARTED) {
            throw std::runtime_error(
                StackChanExpressionOutcomeName(outcome));
        }
    }

    void StartXcBodyBehavior(
            const std::string& behavior_id,
            const char* success_subtype) {
        StartXcBodyExpression(
            behavior_id, "curious", success_subtype);
    }

    void StartXcBodyKnock(const std::string& behavior_id) {
        StartXcBodyBehavior(behavior_id, "knock_complete");
    }

    void StartXcBodyAttention(const std::string& behavior_id) {
        StartXcBodyBehavior(behavior_id, "attention_complete");
    }

    void FinishExpression(
            StackChanExpressionOutcome outcome,
            uint64_t now_us) {
        if (!expression_active_.load(std::memory_order_acquire)) {
            return;
        }

        const ExpressionStep step = expression_step_.load(
            std::memory_order_acquire);
        if (step != ExpressionStep::RESTORING_FACE) {
            expression_finish_outcome_ = outcome;
            expression_face_restore_deadline_us_ =
                now_us + EXPRESSION_FACE_RESTORE_TIMEOUT_US;
            expression_step_.store(
                ExpressionStep::RESTORING_FACE,
                std::memory_order_release);
        }
        bool face_restored = true;
        if (FaceDisplayAllowed()) {
            face_restored = RestoreFaceForCurrentState();
        } else {
            HideFace();
        }
        if (!face_restored) {
            if (now_us < expression_face_restore_deadline_us_) {
                return;
            }
            HideFace();
            if (expression_finish_outcome_ !=
                    StackChanExpressionOutcome::SAFE_RETURN_FAILED) {
                expression_finish_outcome_ =
                    StackChanExpressionOutcome::UNAVAILABLE;
            }
        }
        if (!expression_active_.exchange(false, std::memory_order_acq_rel)) {
            return;
        }
        expression_abort_requested_.store(false, std::memory_order_release);
        if (expression_finish_outcome_ ==
                StackChanExpressionOutcome::SAFE_RETURN_FAILED) {
            physical_motion_unavailable_.store(
                true, std::memory_order_release);
        }
        ESP_LOGI(
            TAG,
            "Expression terminal: name=%s outcome=%s face_restored=%d",
            expression_name_.c_str(),
            StackChanExpressionOutcomeName(expression_finish_outcome_),
            face_restored ? 1 : 0);
        physical_behavior_owner_.store(
            PhysicalBehaviorOwner::IDLE, std::memory_order_release);
        const auto invocation = expression_invocation_.load(
            std::memory_order_acquire);
        if (invocation == ExpressionInvocation::PREVIEW) {
            expression_preview_result_.store(
                expression_finish_outcome_, std::memory_order_relaxed);
            expression_preview_result_ready_.store(
                true, std::memory_order_release);
        } else if (invocation == ExpressionInvocation::BEHAVIOR) {
            const char* subtype = expression_finish_outcome_ ==
                    StackChanExpressionOutcome::COMPLETED
                ? expression_behavior_success_subtype_
                : "behavior_failed";
            Application::GetInstance().SendStackChanEvent(
                "behavior",
                subtype,
                (now_us - expression_started_us_) / 1000ULL,
                expression_behavior_id_.c_str());
        }
        Application::GetInstance().ResumeDeferredAudioPlayback();
    }

    void BeginExpressionRecovery(
            StackChanExpressionOutcome outcome,
            uint64_t now_us,
            const char* reason) {
        expression_recovery_outcome_ = outcome;
        // Do not leave the final GIF frame visible during motor recovery.
        const bool face_restored = ShowIdleFace();
        ESP_LOGW(
            TAG,
            "Expression recovery started: name=%s reason=%s outcome=%s "
            "step_index=%u face_restored=%d",
            expression_name_.c_str(), reason,
            StackChanExpressionOutcomeName(outcome),
            static_cast<unsigned>(expression_step_index_),
            face_restored ? 1 : 0);
        if (PhysicalMotionConfirmedAt(
                kStackChanExpressionIdleYaw,
                kStackChanExpressionIdlePitch,
                "recovery entry")) {
            FinishExpression(outcome, now_us);
            return;
        }
        expression_recovery_deadline_us_ =
            now_us + EXPRESSION_RECOVERY_TIMEOUT_US;
        expression_recovery_retry_at_us_ =
            now_us + EXPRESSION_RECOVERY_RETRY_INTERVAL_US;
        StartMeasuredIdleRecovery();
        expression_step_.store(
            ExpressionStep::RECOVERING_TO_IDLE,
            std::memory_order_release);
    }

    void StartAuthoredExpression(uint64_t now_us) {
        if (!StartExpressionAnimation(expression_recipe_.animation)) {
            FinishExpression(
                StackChanExpressionOutcome::MOTION_FAILED, now_us);
            return;
        }
        HandleScreenSaverUserInteraction();
        display_->UpdateStatusBar(true);
        if (!WriteHeadCurve(expression_recipe_.steps[0])) {
            BeginExpressionRecovery(
                StackChanExpressionOutcome::MOTION_FAILED,
                now_us,
                "first_curve_start_failed");
            return;
        }
        expression_step_index_ = 0;
        const uint64_t motion_duration_us =
            static_cast<uint64_t>(StackChanExpressionRecipeDurationMs(
                expression_recipe_)) * 1000ULL;
        expression_execution_deadline_us_ =
            static_cast<uint64_t>(esp_timer_get_time()) +
            std::max(motion_duration_us, EXPRESSION_FACE_DURATION_US) +
            EXPRESSION_EXECUTION_MARGIN_US;
        expression_step_.store(
            ExpressionStep::RUNNING_CURVE,
            std::memory_order_release);
    }

    void StartNextExpressionStep(uint64_t now_us) {
        ++expression_step_index_;
        if (expression_step_index_ >= expression_recipe_.step_count) {
            if (ExpressionAnimationComplete()) {
                FinishExpression(
                    StackChanExpressionOutcome::COMPLETED, now_us);
            } else {
                expression_step_.store(
                    ExpressionStep::WAITING_FOR_FACE,
                    std::memory_order_release);
            }
            return;
        }
        const auto& next = expression_recipe_.steps[expression_step_index_];
        if (next.type == StackChanExpressionStepType::PAUSE) {
            expression_hold_until_us_ = now_us +
                static_cast<uint64_t>(next.duration_ms) * 1000ULL;
            expression_step_.store(
                ExpressionStep::PAUSING, std::memory_order_release);
            return;
        }
        if (!WriteHeadCurve(next)) {
            BeginExpressionRecovery(
                StackChanExpressionOutcome::MOTION_FAILED,
                now_us,
                "curve_start_failed");
            return;
        }
        expression_step_.store(
            ExpressionStep::RUNNING_CURVE, std::memory_order_release);
    }

    StackChanExpressionOutcome StartExpression(
            const std::string& name,
            const StackChanExpressionRecipe& recipe,
            ExpressionInvocation invocation,
            const std::string& behavior_id = "",
            const char* success_subtype = nullptr) {
        std::string error;
        if (!IsStackChanExpressionRecipeName(name) ||
            !ValidateStackChanExpressionRecipe(recipe, error)) {
            return StackChanExpressionOutcome::INVALID_RECIPE;
        }
        if (physical_motion_unavailable_.load(std::memory_order_acquire)) {
            return StackChanExpressionOutcome::UNAVAILABLE;
        }
        if (!servo_ok_ || motion_driver_ == nullptr) {
            return StackChanExpressionOutcome::UNAVAILABLE;
        }
        if (!motion_driver_->SupportsCurve()) {
            return StackChanExpressionOutcome::UNSUPPORTED_DRIVER;
        }

        const bool preview = invocation == ExpressionInvocation::PREVIEW;
        if (!ExpressionAdmissionIdle(preview)) {
            return StackChanExpressionOutcome::BUSY;
        }

        PhysicalBehaviorOwner expected = PhysicalBehaviorOwner::IDLE;
        if (!physical_behavior_owner_.compare_exchange_strong(
                expected,
                PhysicalBehaviorOwner::EXPRESSION,
                std::memory_order_acq_rel)) {
            return StackChanExpressionOutcome::BUSY;
        }
        if (!ExpressionEnvironmentOwned(preview)) {
            physical_behavior_owner_.store(
                PhysicalBehaviorOwner::IDLE, std::memory_order_release);
            Application::GetInstance().ResumeDeferredAudioPlayback();
            return StackChanExpressionOutcome::BUSY;
        }

        power_save_timer_->WakeUp();
        expression_recipe_ = recipe;
        expression_step_index_ = 0;
        expression_name_ = name;
        expression_invocation_.store(
            invocation, std::memory_order_relaxed);
        expression_behavior_id_ = behavior_id;
        expression_behavior_success_subtype_ = success_subtype;
        expression_abort_requested_.store(false, std::memory_order_relaxed);
        if (preview) {
            expression_preview_result_ready_.store(
                false, std::memory_order_relaxed);
        }
        const uint64_t now_us = esp_timer_get_time();
        expression_started_us_ = now_us;
        expression_startup_deadline_us_ =
            now_us + EXPRESSION_STARTUP_TIMEOUT_US;
        expression_step_.store(
            ExpressionStep::STARTING, std::memory_order_relaxed);
        expression_active_.store(true, std::memory_order_release);

        // Close the admission/callback handoff: a state transition that
        // raced the first check either made the environment busy or set the
        // abort flag after expression_active_ became visible.
        if (!ExpressionEnvironmentOwned(preview) ||
            expression_abort_requested_.load(std::memory_order_acquire)) {
            expression_active_.store(false, std::memory_order_release);
            expression_abort_requested_.store(false, std::memory_order_release);
            physical_behavior_owner_.store(
                PhysicalBehaviorOwner::IDLE, std::memory_order_release);
            Application::GetInstance().ResumeDeferredAudioPlayback();
            return StackChanExpressionOutcome::BUSY;
        }
        return StackChanExpressionOutcome::STARTED;
    }

    void AdvanceExpression() {
        if (!expression_active_.load(std::memory_order_acquire)) {
            return;
        }
        const uint64_t now_us = esp_timer_get_time();
        ExpressionStep step = expression_step_.load(
            std::memory_order_acquire);
        if (step != ExpressionStep::STARTING &&
            step != ExpressionStep::RESTORING_FACE &&
            step != ExpressionStep::RECOVERING_TO_IDLE &&
            expression_abort_requested_.load(std::memory_order_acquire)) {
            BeginExpressionRecovery(
                StackChanExpressionOutcome::INTERRUPTED,
                now_us,
                "abort_requested");
            return;
        }
        if (step == ExpressionStep::RUNNING_CURVE &&
            motion_driver_->ConsumeCurveFailure()) {
            BeginExpressionRecovery(
                StackChanExpressionOutcome::MOTION_FAILED,
                now_us,
                "curve_driver_failed");
            return;
        }
        if (step != ExpressionStep::STARTING &&
            step != ExpressionStep::CENTERING &&
            step != ExpressionStep::RESTORING_FACE &&
            step != ExpressionStep::RECOVERING_TO_IDLE &&
            now_us >= expression_execution_deadline_us_) {
            BeginExpressionRecovery(
                StackChanExpressionOutcome::MOTION_FAILED,
                now_us,
                "execution_deadline");
            return;
        }

        switch (step) {
            case ExpressionStep::STARTING:
                if (expression_abort_requested_.load(
                        std::memory_order_acquire)) {
                    BeginExpressionRecovery(
                        StackChanExpressionOutcome::INTERRUPTED,
                        now_us,
                        "startup_abort_requested");
                } else if (PhysicalMotionCachedAt(
                               kStackChanExpressionIdleYaw,
                               kStackChanExpressionIdlePitch) &&
                           PhysicalMotionConfirmedAt(
                               kStackChanExpressionIdleYaw,
                               kStackChanExpressionIdlePitch)) {
                    StartAuthoredExpression(now_us);
                } else if (!SynchronizePhysicalMotionPosition() ||
                           !WriteHeadAngles(
                               kStackChanExpressionIdleYaw,
                               kStackChanExpressionIdlePitch,
                               XC_BODY_BEHAVIOR_SPEED_DPS)) {
                    BeginExpressionRecovery(
                        StackChanExpressionOutcome::MOTION_FAILED,
                        now_us,
                        "startup_center_failed");
                } else {
                    expression_step_.store(
                        ExpressionStep::CENTERING,
                        std::memory_order_release);
                }
                break;
            case ExpressionStep::CENTERING:
                if (PhysicalMotionCachedAt(
                        kStackChanExpressionIdleYaw,
                        kStackChanExpressionIdlePitch) &&
                    PhysicalMotionConfirmedAt(
                        kStackChanExpressionIdleYaw,
                        kStackChanExpressionIdlePitch)) {
                    StartAuthoredExpression(now_us);
                } else if (now_us >= expression_startup_deadline_us_) {
                    BeginExpressionRecovery(
                        StackChanExpressionOutcome::MOTION_FAILED,
                        now_us,
                        "startup_deadline");
                }
                break;
            case ExpressionStep::RUNNING_CURVE: {
                const auto& curve =
                    expression_recipe_.steps[expression_step_index_];
                const auto& end = curve.points[3];
                const bool final_curve = expression_step_index_ + 1 >=
                    expression_recipe_.step_count;
                if (PhysicalMotionInactive() &&
                    (!final_curve ||
                     PhysicalMotionConfirmedAt(end.yaw, end.pitch))) {
                    StartNextExpressionStep(now_us);
                }
                break;
            }
            case ExpressionStep::PAUSING:
                if (now_us >= expression_hold_until_us_) {
                    StartNextExpressionStep(now_us);
                }
                break;
            case ExpressionStep::WAITING_FOR_FACE:
                if (ExpressionAnimationComplete()) {
                    FinishExpression(
                        StackChanExpressionOutcome::COMPLETED, now_us);
                }
                break;
            case ExpressionStep::RESTORING_FACE:
                FinishExpression(expression_finish_outcome_, now_us);
                break;
            case ExpressionStep::RECOVERING_TO_IDLE: {
                const bool recovery_deadline_reached =
                    now_us >= expression_recovery_deadline_us_;
                const bool recovery_retry_due =
                    !recovery_deadline_reached &&
                    now_us >= expression_recovery_retry_at_us_ &&
                    PhysicalMotionInactive();
                const char* diagnostic_context = recovery_deadline_reached
                    ? "safe return deadline"
                    : recovery_retry_due ? "idle return retry" : nullptr;
                if (PhysicalMotionConfirmedAt(
                        kStackChanExpressionIdleYaw,
                        kStackChanExpressionIdlePitch,
                        diagnostic_context)) {
                    FinishExpression(expression_recovery_outcome_, now_us);
                } else if (recovery_deadline_reached) {
                    FinishExpression(
                        StackChanExpressionOutcome::SAFE_RETURN_FAILED,
                        now_us);
                } else if (recovery_retry_due) {
                    StartMeasuredIdleRecovery();
                    expression_recovery_retry_at_us_ = now_us +
                        EXPRESSION_RECOVERY_RETRY_INTERVAL_US;
                }
                break;
            }
        }
    }

    bool StartTouchReaction() {
        StackChanExpressionRecipe recipe;
        if (!LoadTouchRecipe(recipe)) {
            ESP_LOGW(TAG, "Touch reaction recipe is unavailable");
            return false;
        }
        return StartExpression(
            "touch", recipe, ExpressionInvocation::TOUCH) ==
            StackChanExpressionOutcome::STARTED;
    }

    static void ServoTaskTrampoline(void* arg) {
        static_cast<StackChanBoard*>(arg)->ServoTaskMain();
    }

    void ServoTaskMain() {
        while (true) {
            if (!servo_ok_ || motion_driver_ == nullptr) {
                AdvanceExpression();
                vTaskDelay(pdMS_TO_TICKS(MOTION_TICK_MS));
                continue;
            }
            motion_driver_->Tick();
            AdvanceExpression();
            MaybeAutoReleaseTorque();
            taskYIELD();
        }
    }
    static inline char Si12tChLevelChar(uint8_t raw, int ch) {
        uint8_t v = (raw >> (ch * 2)) & 0x3;
        return "0LMH"[v];
    }

    // Emit the touch event log line. press_zones/press_raw are the
    // rising-edge snapshot (= the touch the user actually made);
    // release_raw is whatever the sensor reports at the falling edge
    // (normally 0x00 — anything else hints at debounce / hysteresis quirks).
    // ch=%c%c%c%c spells CH1〜CH4 levels using 0/L/M/H. CH4 is unused on
    // stack-chan (the head has 3 zones), so anything non-0 on CH4 is a
    // wiring noise / EMI signature worth investigating.
    void LogTouchEvent(const char* event_name, uint64_t duration_ms) {
        ESP_LOGI(TAG,
                 "touch event: %s start_zones=%d%d%d start_raw=0x%02X ch=%c%c%c%c "
                 "release_raw=0x%02X duration=%u ms",
                 event_name,
                 press_start_zones_[0], press_start_zones_[1], press_start_zones_[2],
                 press_start_output1_raw_,
                 Si12tChLevelChar(press_start_output1_raw_, 0),
                 Si12tChLevelChar(press_start_output1_raw_, 1),
                 Si12tChLevelChar(press_start_output1_raw_, 2),
                 Si12tChLevelChar(press_start_output1_raw_, 3),
                 last_output1_raw_,
                 (unsigned)duration_ms);
    }

    void HandleTap(uint64_t duration_ms) {
        if (!touch_sensor_enabled_.load(std::memory_order_acquire)) {
            return;
        }
        if (!StartTouchReaction()) {
            return;
        }
        LogTouchEvent("TAP", duration_ms);
        Application::GetInstance().SendStackChanEvent(
            "touch", "tap", duration_ms);
        last_event_ = TouchEvent::TAP;
        last_event_us_ = esp_timer_get_time();
    }

    void HandleStroke(uint64_t duration_ms) {
        if (!touch_sensor_enabled_.load(std::memory_order_acquire)) {
            return;
        }
        if (!StartTouchReaction()) {
            return;
        }
        LogTouchEvent("STROKE", duration_ms);
        Application::GetInstance().SendStackChanEvent(
            "touch", "stroke", duration_ms);
        last_event_ = TouchEvent::STROKE;
        last_event_us_ = esp_timer_get_time();
    }

    // 200 ms periodic poll. Reads the sensor, applies a 2-sample debounce on
    // the OR of the three head zones, and emits TAP/STROKE on falling edges.
    static void TouchPollCb(void* arg) {
        StackChanBoard* self = static_cast<StackChanBoard*>(arg);
        self->TouchPollTick();
    }

    void TouchPollTick() {
        if (!si12t_ok_ || si12t_ == nullptr) {
            return;
        }
        if (Application::GetInstance().GetDeviceState() ==
            kDeviceStateUpgrading) {
            touch_pressed_prev_ = false;
            touch_pressed_pending_ = false;
            touch_pending_count_ = 0;
            touch_press_start_us_ = 0;
            return;
        }
        Si12T::TouchState s = si12t_->ReadTouchState();
        if (!s.ok) {
            return;
        }
        // Snapshot for MCP visibility.
        last_output1_raw_ = s.output1_raw;
        last_zone_snapshot_[0] = s.zone[0];
        last_zone_snapshot_[1] = s.zone[1];
        last_zone_snapshot_[2] = s.zone[2];

        bool any_pressed = s.zone[0] || s.zone[1] || s.zone[2];

        // Asymmetric debounce:
        //   press   confirm = 2 samples ( 200 ms) — fast tap detection
        //   release confirm = 4 samples ( 400 ms) — bridges Si12T recalibration
        //                                            and finger-glide gaps that
        //                                            otherwise cut a stroke
        //                                            short and mis-classify it
        //                                            as a tap.
        // Keeping a press "sticky" through brief no-press blips is essential
        // for the stroke gesture to reach STROKE_MIN_MS.
        if (any_pressed == touch_pressed_pending_) {
            touch_pending_count_++;
        } else {
            touch_pending_count_ = 1;
            touch_pressed_pending_ = any_pressed;
        }
        const int needed = touch_pressed_pending_ ? 2 : 4;
        if (touch_pending_count_ < needed) {
            return;  // not yet debounced
        }

        bool now = touch_pressed_pending_;
        if (now == touch_pressed_prev_) {
            return;  // no edge
        }

        uint64_t now_us = esp_timer_get_time();

        if (now) {
            power_save_timer_->WakeUp();
            head_touch_woke_screensaver_ =
                HandleScreenSaverUserInteraction();
            // Rising edge. Capture the sensor state for the falling-edge
            // log either way — without this, a press that begins during
            // the post-reaction cooldown and is held until the cooldown
            // expires would log the previous touch's start_zones /
            // start_raw on its falling edge, exactly the
            // repeated-touch / noise-overlap scenario this logging is
            // meant to clarify.
            press_start_zones_[0] = s.zone[0];
            press_start_zones_[1] = s.zone[1];
            press_start_zones_[2] = s.zone[2];
            press_start_output1_raw_ = s.output1_raw;
            if (now_us < cooldown_until_us_) {
                // Suppress press event while in post-reaction cooldown.
                touch_pressed_prev_ = now;
                touch_press_start_us_ = now_us;
                return;
            }
            touch_pressed_prev_ = true;
            touch_press_start_us_ = now_us;
        } else {
            // Falling edge: classify by hold duration.
            touch_pressed_prev_ = false;
            uint64_t duration_ms = (now_us - touch_press_start_us_) / 1000ULL;
            if (head_touch_woke_screensaver_) {
                head_touch_woke_screensaver_ = false;
                return;
            }
            if (now_us < cooldown_until_us_) {
                // We were in cooldown when pressed — drop the release event too.
                return;
            }
            if (duration_ms >= STROKE_MIN_MS) {
                HandleStroke(duration_ms);
            } else {
                HandleTap(duration_ms);
            }
            cooldown_until_us_ = now_us + (uint64_t)COOLDOWN_MS * 1000ULL;
        }
    }

    void InitializeTouchSettings() {
        Settings settings("touch", false);
        bool enabled = settings.GetBool("enabled", true);
        touch_sensor_enabled_.store(enabled, std::memory_order_release);
        ESP_LOGI(TAG, "Touch sensor setting loaded: enabled=%d", enabled ? 1 : 0);
    }

    void InitializeSi12tTouch() {
        ESP_LOGI(TAG, "Init Si12T head-touch sensor (I2C addr 0x%02X)", Si12T::DEFAULT_ADDR);
        si12t_ = std::unique_ptr<Si12T>(new Si12T(i2c_bus_));
        si12t_ok_ = si12t_->Begin();
        if (!si12t_ok_) {
            ESP_LOGW(TAG, "Si12T not detected; head-touch disabled (other features unaffected)");
            si12t_.reset();
            return;
        }

        esp_timer_create_args_t poll_args = {
            .callback = &StackChanBoard::TouchPollCb,
            .arg = this,
            .dispatch_method = ESP_TIMER_TASK,
            .name = "touch_poll",
            .skip_unhandled_events = true,
        };
        ESP_ERROR_CHECK(esp_timer_create(&poll_args, &touch_poll_timer_));
        ESP_ERROR_CHECK(esp_timer_start_periodic(touch_poll_timer_,
                                                 (uint64_t)TOUCH_POLL_MS * 1000));
        ESP_LOGI(TAG, "Si12T touch poll started (%d ms interval)", TOUCH_POLL_MS);
    }

    bool FaceDisplayAllowed() const {
        const DeviceState state =
            Application::GetInstance().GetDeviceState();
        return !settings_open_.load(std::memory_order_acquire) &&
            (state == kDeviceStateIdle ||
             state == kDeviceStateListening ||
             state == kDeviceStateSpeaking);
    }

    bool EnsureFaceObjectLocked() {
        if (face_image_ != nullptr && lv_obj_is_valid(face_image_)) {
            return true;
        }
        lv_obj_t* screen = lv_screen_active();
        if (screen == nullptr) {
            return false;
        }
        face_image_ = lv_image_create(screen);
        if (face_image_ == nullptr) {
            return false;
        }
        lv_obj_align(face_image_, LV_ALIGN_CENTER, 0, 0);
        lv_obj_clear_flag(face_image_, LV_OBJ_FLAG_SCROLLABLE);
        display_->PlaceBehindStatusBarLocked(face_image_);
        return true;
    }

    bool ShowFaceAssetLocked(
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

        face_gif_.reset();
        face_gif_source_ = {};
        face_gif_source_.data = static_cast<const uint8_t*>(data);
        face_gif_source_.data_size = size;
        auto gif = std::make_unique<LvglGif>(&face_gif_source_);
        if (!gif->IsLoaded()) {
            ESP_LOGW(TAG, "Face GIF could not be decoded: %s", asset.c_str());
            return false;
        }
        gif->SetLoopCount(loop_count);
        gif->SetTimelinePlayback(true);
        face_gif_ = std::move(gif);
        face_gif_->SetFrameCallback([this]() {
            if (face_image_ != nullptr && face_gif_ != nullptr) {
                lv_image_set_src(face_image_, face_gif_->image_dsc());
                lv_obj_invalidate(face_image_);
            }
        });
        lv_image_set_src(face_image_, face_gif_->image_dsc());
        lv_image_set_scale(face_image_, 256);
        lv_obj_clear_flag(face_image_, LV_OBJ_FLAG_HIDDEN);
        display_->PlaceBehindStatusBarLocked(face_image_);
        if (play) {
            face_gif_->Start();
        } else {
            face_gif_->Stop();
            lv_obj_invalidate(face_image_);
        }
        return true;
    }

    bool ShowFaceAsset(
            const std::string& asset,
            int32_t loop_count,
            bool play) {
        if (display_ == nullptr || !FaceDisplayAllowed()) {
            return false;
        }
        DisplayLockGuard lock(display_);
        return ShowFaceAssetLocked(asset, loop_count, play);
    }

    bool ShowIdleFace() {
        return ShowFaceAsset("expression-agree.gif", 1, false);
    }

    bool ShowListeningFace() {
        if (ShowFaceAsset("listening.gif", 0, true)) {
            return true;
        }
        // Listening art is optional. Keep recording usable and fall back to
        // the static idle presence when the asset has not shipped yet.
        return ShowIdleFace();
    }

    bool RestoreFaceForCurrentState() {
        return Application::GetInstance().GetDeviceState() ==
                kDeviceStateListening
            ? ShowListeningFace()
            : ShowIdleFace();
    }

    void HideFace() {
        if (display_ == nullptr) {
            return;
        }
        DisplayLockGuard lock(display_);
        face_gif_.reset();
        if (face_image_ != nullptr && lv_obj_is_valid(face_image_)) {
            lv_obj_add_flag(face_image_, LV_OBJ_FLAG_HIDDEN);
        }
        HideScreenSaverLocked();
    }

    bool StartExpressionAnimation(const std::string& animation) {
        if (!IsStackChanExpressionName(animation)) {
            return false;
        }
        return ShowFaceAsset(
            "expression-" + animation + ".gif", 1, true);
    }

    bool ExpressionAnimationComplete() {
        if (display_ == nullptr) {
            return false;
        }
        DisplayLockGuard lock(display_);
        return face_gif_ != nullptr && !face_gif_->IsPlaying();
    }

    bool LoadTouchRecipe(StackChanExpressionRecipe& recipe) {
        const auto stored = LoadStackChanExpressionRecipe("touch", recipe);
        if (stored == StackChanExpressionLoadStatus::OK) {
            return true;
        }
        if (stored == StackChanExpressionLoadStatus::INVALID) {
            return false;
        }

        void* data = nullptr;
        size_t size = 0;
        if (!Assets::GetInstance().GetAssetData(
                "touch.json", data, size) ||
            data == nullptr || size == 0) {
            return false;
        }
        const std::string encoded(
            static_cast<const char*>(data), size);
        cJSON* root = cJSON_ParseWithLength(
            encoded.c_str(), encoded.size());
        std::string error;
        const bool valid = ParseStackChanExpressionRecipe(root, recipe) &&
            ValidateStackChanExpressionRecipe(recipe, error);
        cJSON_Delete(root);
        return valid;
    }

    static bool IsGatewayUrlForced() {
#if defined(CONFIG_FORCE_DEFAULT_WEBSOCKET_URL) && defined(CONFIG_DEFAULT_WEBSOCKET_URL)
        return CONFIG_DEFAULT_WEBSOCKET_URL[0] != '\0';
#else
        return false;
#endif
    }

    static bool IsGatewayFallbackUrlForced() {
#if defined(CONFIG_FORCE_DEFAULT_WEBSOCKET_URL) && defined(CONFIG_DEFAULT_WEBSOCKET_FALLBACK_URL)
        return CONFIG_DEFAULT_WEBSOCKET_FALLBACK_URL[0] != '\0';
#else
        return false;
#endif
    }

    static bool IsGatewayTokenForced() {
#if defined(CONFIG_FORCE_DEFAULT_WEBSOCKET_URL) && defined(CONFIG_DEFAULT_WEBSOCKET_TOKEN)
        return CONFIG_DEFAULT_WEBSOCKET_TOKEN[0] != '\0';
#else
        return false;
#endif
    }

    static bool IsGatewayForceMode() {
        return IsGatewayUrlForced() || IsGatewayFallbackUrlForced() || IsGatewayTokenForced();
    }

    static bool IsGatewayDiscoveryCompiledIn() {
#if defined(CONFIG_STACKCHAN_MDNS_DISCOVERY) && CONFIG_STACKCHAN_MDNS_DISCOVERY
        return true;
#else
        return false;
#endif
    }

    static bool IsGatewayDiscoveryEnabled(const std::string& url) {
        // Only a forced primary URL suppresses the NVS/mDNS candidate path.
        return IsGatewayDiscoveryCompiledIn() && !IsGatewayUrlForced() && url.empty();
    }

    static void AddGatewayForcedKeys(cJSON* root) {
        cJSON* forced_keys = cJSON_CreateArray();
        if (IsGatewayUrlForced()) {
            cJSON_AddItemToArray(forced_keys, cJSON_CreateString("url"));
        }
        if (IsGatewayFallbackUrlForced()) {
            cJSON_AddItemToArray(forced_keys, cJSON_CreateString("fallback_url"));
        }
        if (IsGatewayTokenForced()) {
            cJSON_AddItemToArray(forced_keys, cJSON_CreateString("token"));
        }
        cJSON_AddItemToObject(root, "forced_keys", forced_keys);
    }

    static void AddGatewayForcedKeyNote(cJSON* notes,
                                        const char* key,
                                        bool provided,
                                        bool forced) {
        if (!provided || !forced) {
            return;
        }
        std::string note = std::string(key) +
            " is overridden by the Kconfig default at connect time until a non-force build is flashed.";
        cJSON_AddItemToArray(notes, cJSON_CreateString(note.c_str()));
    }

    static void AddGatewayRuntimeContext(cJSON* root, const std::string& url) {
        bool force_mode = IsGatewayForceMode();
        cJSON_AddBoolToObject(root, "force_mode", force_mode);
        AddGatewayForcedKeys(root);
        cJSON_AddBoolToObject(root, "discovery_compiled_in", IsGatewayDiscoveryCompiledIn());
        cJSON_AddBoolToObject(root, "discovery_enabled", IsGatewayDiscoveryEnabled(url));
    }

    void RegisterMcpTools() {
        auto& mcp_server = McpServer::GetInstance();
        ESP_LOGI(TAG, "Registering StackChan MCP tools...");

        mcp_server.AddTool(
            "self.gateway_config.get",
            "Read the NVS-backed WebSocket gateway connection settings. "
            "Returns websocket.url, websocket.fallback_url, token_set (never "
            "the token value), forced_keys, force_mode, discovery_enabled, "
            "and the current connected_url when a WebSocket candidate is "
            "connected. Empty websocket.url enables mDNS discovery when "
            "discovery support is compiled in and the primary URL is not "
            "forced; websocket.fallback_url is tried after discovery and is "
            "suitable for an out-of-LAN relay. forced_keys lists any of url, "
            "fallback_url, and token that a non-empty Kconfig default "
            "overrides at connect time; force_mode=true means at least one "
            "key is forced.",
            PropertyList(),
            [](const PropertyList&) -> ReturnValue {
                Settings settings("websocket", false);
                std::string url = settings.GetString("url");
                std::string fallback_url = settings.GetString("fallback_url");
                bool token_set = !settings.GetString("token").empty();
                std::string connected_url = Application::GetInstance().GetConnectedGatewayUrl();

                cJSON* root = cJSON_CreateObject();
                cJSON_AddStringToObject(root, "url", url.c_str());
                cJSON_AddStringToObject(root, "fallback_url", fallback_url.c_str());
                cJSON_AddBoolToObject(root, "token_set", token_set);
                AddGatewayRuntimeContext(root, url);
                if (connected_url.empty()) {
                    cJSON_AddNullToObject(root, "connected_url");
                } else {
                    cJSON_AddStringToObject(root, "connected_url", connected_url.c_str());
                }
                return root;
            });

        mcp_server.AddTool(
            "self.gateway_config.set",
            "Update the NVS-backed WebSocket gateway connection settings. "
            "Optional string fields: url, fallback_url, token. At least one "
            "field must be provided. Passing an empty string clears that NVS "
            "key. Leave url empty to enable mDNS discovery on the next "
            "reconnect when discovery support is compiled in and the primary "
            "URL is not forced; fallback_url is tried after discovery and is "
            "suitable for an out-of-LAN relay. The change is persisted but "
            "does not disconnect, reconnect, or reboot the device; it takes "
            "effect on the next reconnect. forced_keys lists any of url, "
            "fallback_url, and token that a non-empty Kconfig default "
            "overrides at connect time; force_mode=true means at least one "
            "key is forced, so updates to those keys are ignored until a "
            "non-force build is flashed.",
            PropertyList({Property("url", kPropertyTypeString, std::string()),
                          Property("fallback_url", kPropertyTypeString, std::string()),
                          Property("token", kPropertyTypeString, std::string())}),
            [](const PropertyList& properties) -> ReturnValue {
                const auto& url_property = properties["url"];
                const auto& fallback_url_property = properties["fallback_url"];
                const auto& token_property = properties["token"];
                bool url_provided = url_property.was_provided();
                bool fallback_url_provided = fallback_url_property.was_provided();
                bool token_provided = token_property.was_provided();
                if (!url_provided && !fallback_url_provided && !token_provided) {
                    throw std::invalid_argument(
                        "At least one of url, fallback_url, or token must be provided");
                }

                Settings settings("websocket", true);
                std::string url = settings.GetString("url");
                std::string fallback_url = settings.GetString("fallback_url");
                std::string token = settings.GetString("token");

                cJSON* updated_keys = cJSON_CreateArray();
                auto apply_string = [&](const char* response_key,
                                        const char* nvs_key,
                                        bool provided,
                                        const std::string& requested_value,
                                        std::string& current_value) {
                    if (!provided) {
                        return;
                    }
                    if (requested_value.empty()) {
                        settings.EraseKey(nvs_key);
                        current_value.clear();
                    } else {
                        settings.SetString(nvs_key, requested_value);
                        current_value = requested_value;
                    }
                    cJSON_AddItemToArray(updated_keys, cJSON_CreateString(response_key));
                };

                apply_string("url", "url", url_provided,
                             url_property.value<std::string>(), url);
                apply_string("fallback_url", "fallback_url", fallback_url_provided,
                             fallback_url_property.value<std::string>(), fallback_url);
                apply_string("token", "token", token_provided,
                             token_property.value<std::string>(), token);

                cJSON* root = cJSON_CreateObject();
                cJSON_AddBoolToObject(root, "ok", true);
                cJSON_AddItemToObject(root, "updated_keys", updated_keys);
                cJSON_AddStringToObject(root, "url", url.c_str());
                cJSON_AddStringToObject(root, "fallback_url", fallback_url.c_str());
                cJSON_AddBoolToObject(root, "token_set", !token.empty());
                AddGatewayRuntimeContext(root, url);
                cJSON_AddStringToObject(root, "takes_effect", "next_reconnect");

                cJSON* notes = cJSON_CreateArray();
                if (!url.empty() && !IsGatewayUrlForced()) {
                    cJSON_AddItemToArray(
                        notes,
                        cJSON_CreateString(
                            "mDNS discovery is disabled until websocket.url is cleared."));
                }
                AddGatewayForcedKeyNote(notes, "url", url_provided, IsGatewayUrlForced());
                AddGatewayForcedKeyNote(notes, "fallback_url", fallback_url_provided,
                                        IsGatewayFallbackUrlForced());
                AddGatewayForcedKeyNote(notes, "token", token_provided, IsGatewayTokenForced());
                cJSON_AddItemToObject(root, "notes", notes);
                return root;
            });

        mcp_server.AddTool(
            "self.robot.get_touch_sensor_enabled",
            "Read the NVS-backed head-touch sensor enable flag. When disabled, "
            "HandleTap / HandleStroke skip both the local expression and the "
            "stackchan/event emission.",
            PropertyList(),
            [this](const PropertyList&) -> ReturnValue {
                cJSON* root = cJSON_CreateObject();
                cJSON_AddBoolToObject(root, "enabled",
                                      touch_sensor_enabled_.load(std::memory_order_acquire));
                return root;
            });

        mcp_server.AddTool(
            "self.robot.set_touch_sensor_enabled",
            "Update the NVS-backed head-touch sensor enable flag. Disabling "
            "takes effect immediately and persists across reboot; subsequent "
            "HandleTap / HandleStroke calls skip both the local expression "
            "and the stackchan/event emission.",
            PropertyList({Property("enabled", kPropertyTypeBoolean)}),
            [this](const PropertyList& properties) -> ReturnValue {
                bool enabled = properties["enabled"].value<bool>();
                Settings settings("touch", true);
                settings.SetBool("enabled", enabled);
                touch_sensor_enabled_.store(enabled, std::memory_order_release);

                cJSON* root = cJSON_CreateObject();
                cJSON_AddBoolToObject(root, "ok", true);
                cJSON_AddBoolToObject(root, "enabled", enabled);
                cJSON_AddStringToObject(root, "takes_effect", "immediate");
                cJSON_AddStringToObject(root, "persistence", "nvs");
                return root;
            });

        mcp_server.AddTool(
            "self.robot.xc_body_behavior",
            "Run one XC Body physical behavior locally. Knock and attention "
            "play curious; expression plays one named saved recipe. Every "
            "kind returns safely to idle before publishing completion.",
            PropertyList({
                Property("behavior_id", kPropertyTypeString),
                Property("kind", kPropertyTypeString),
                Property(
                    "expression", kPropertyTypeString, std::string()),
            }),
            [this](const PropertyList& properties) -> ReturnValue {
                const std::string& behavior_id =
                    properties["behavior_id"].value<std::string>();
                const std::string& kind =
                    properties["kind"].value<std::string>();
                if (kind == "knock") {
                    StartXcBodyKnock(behavior_id);
                } else if (kind == "attention") {
                    StartXcBodyAttention(behavior_id);
                } else if (kind == "expression") {
                    const std::string& expression =
                        properties["expression"].value<std::string>();
                    StartXcBodyExpression(
                        behavior_id,
                        expression,
                        "expression_complete");
                } else {
                    throw std::invalid_argument(
                        "kind must be knock, attention, or expression");
                }

                cJSON* root = cJSON_CreateObject();
                cJSON_AddBoolToObject(root, "ok", true);
                cJSON_AddBoolToObject(root, "started", true);
                cJSON_AddStringToObject(
                    root, "behavior_id", behavior_id.c_str());
                cJSON_AddStringToObject(root, "kind", kind.c_str());
                return root;
            });

        mcp_server.AddTool(
            "self.robot.xc_body_knock",
            "Play the saved curious expression and publish the existing "
            "knock completion event after safe return.",
            PropertyList({Property(
                "behavior_id", kPropertyTypeString)}),
            [this](const PropertyList& properties) -> ReturnValue {
                const std::string& behavior_id =
                    properties["behavior_id"].value<std::string>();
                StartXcBodyKnock(behavior_id);

                cJSON* root = cJSON_CreateObject();
                cJSON_AddBoolToObject(root, "ok", true);
                cJSON_AddBoolToObject(root, "started", true);
                cJSON_AddStringToObject(
                    root, "behavior_id", behavior_id.c_str());
                return root;
            });

        // Set head angles (yaw, pitch in degrees)
        // SCS0009: 1 step = 0.3125 degrees, so 1 degree = 3.2 steps (= 16/5)
        // yaw: -90..90 degrees (no hardware restriction). pitch: two-tier
        // guard — see SAFE_PITCH_MIN/MAX (hard clamp for mechanical safety)
        // and RECOMMENDED_PITCH_MIN/MAX (M5Stack-documented operating sweet
        // spot) above, plus Issue #80 / #98.
        mcp_server.AddTool(
            "self.robot.set_head_angles",
            "Set the head angles of the robot. yaw: horizontal (-90 to 90). pitch: vertical. M5Stack-recommended operating range is 5 to 85 degrees per https://docs.m5stack.com/en/StackChan (\"Motion Angle Notice\"). The firmware also accepts values up to 88 degrees (the hard clamp guards against the audible sub-stall observed at pitch=89 on real hardware), but values outside 5-85 degrees are not officially endorsed and may stress the servo over time. Requests below 0 degrees or above 88 degrees are silently clamped with an ESP_LOGW. Optional speed_dps: angular speed in degrees per second. If omitted or zero, the existing duration-based default applies; positive values below 15 dps are clamped to the step-safe floor. See README \"Hardware safety notes\".",
            // Pitch schema range is intentionally permissive across the
            // entire `int` value range (std::numeric_limits<int>::min/max):
            // the authoritative Tier 1 enforcement lives in the handler
            // below (silent clamp to [SAFE_PITCH_MIN, SAFE_PITCH_MAX] with
            // ESP_LOGW). Any narrower range would cause McpServer::Property
            // to reject sufficiently-extreme requests (e.g. pitch=200 or
            // pitch=INT_MIN) before the handler can run, leaving the
            // Tier 1 clamp / log unreachable for those callers and
            // contradicting the tool-description / README claim that
            // out-of-range requests are silently clamped with ESP_LOGW —
            // see Issue #98 (three adversarial-review rounds zeroed in on
            // this exact contract, including the int-boundary corners) and
            // PR #81's defense-in-depth requirement that every servo-write
            // boundary be guarded inside the firmware regardless of
            // caller behavior.
            PropertyList({Property("yaw", kPropertyTypeInteger, 0, -90, 90),
                          Property("pitch", kPropertyTypeInteger, 0,
                                   std::numeric_limits<int>::min(),
                                   std::numeric_limits<int>::max()),
                          Property("speed_dps", kPropertyTypeInteger, 0,
                                   std::numeric_limits<int>::min(),
                                   std::numeric_limits<int>::max())}),
            [this](const PropertyList& properties) -> ReturnValue {
                if (physical_motion_unavailable_.load(
                        std::memory_order_acquire)) {
                    throw std::runtime_error("head motion is unavailable");
                }
                PhysicalBehaviorOwner expected = PhysicalBehaviorOwner::IDLE;
                if (!physical_behavior_owner_.compare_exchange_strong(
                        expected,
                        PhysicalBehaviorOwner::RAW,
                        std::memory_order_acq_rel)) {
                    throw std::runtime_error(
                        "another physical behavior is active");
                }
                int yaw = properties["yaw"].value<int>();
                int pitch = properties["pitch"].value<int>();
                int speed_dps = properties["speed_dps"].value<int>();
                // Issue #80 / #98: two-tier pitch guard.
                //
                // Tier 1 (hard clamp): silently clamp to [SAFE_PITCH_MIN,
                // SAFE_PITCH_MAX] and ESP_LOGW. PitchDegToPos() clamps
                // again at the servo-write boundary (defense-in-depth);
                // doing it here lets us log the original out-of-range
                // value. See the SAFE_PITCH_MIN/MAX comment block above.
                if (pitch < SAFE_PITCH_MIN) {
                    ESP_LOGW(TAG, "set_head_angles: pitch=%d below SAFE_PITCH_MIN=%d, clamping (servo end-stop protection)",
                             pitch, SAFE_PITCH_MIN);
                    pitch = SAFE_PITCH_MIN;
                }
                if (pitch > SAFE_PITCH_MAX) {
                    ESP_LOGW(TAG, "set_head_angles: pitch=%d above SAFE_PITCH_MAX=%d, clamping (servo end-stop protection)",
                             pitch, SAFE_PITCH_MAX);
                    pitch = SAFE_PITCH_MAX;
                }
                // Tier 2 (recommended-range soft signal): inside the hard
                // clamp but outside the M5Stack-documented operating
                // range — accept the value and emit an ESP_LOGI so callers
                // can notice the deviation without blocking the motion.
                if (pitch < RECOMMENDED_PITCH_MIN || pitch > RECOMMENDED_PITCH_MAX) {
                    ESP_LOGI(TAG, "set_head_angles: pitch=%d outside M5Stack-recommended range %d..%d (within hard clamp %d..%d); acceptable but not officially endorsed",
                             pitch, RECOMMENDED_PITCH_MIN, RECOMMENDED_PITCH_MAX,
                             SAFE_PITCH_MIN, SAFE_PITCH_MAX);
                }
                int yaw_pos = YawDegToPos(yaw);
                int pitch_pos = PitchDegToPos(pitch);
                bool motion_started;
                if (speed_dps > 0) {
                    motion_started = WriteHeadAngles(yaw, pitch, speed_dps);
                } else {
                    motion_started = WriteHeadAngles(yaw, pitch);
                }
                physical_behavior_owner_.store(
                    PhysicalBehaviorOwner::IDLE,
                    std::memory_order_release);
                if (!motion_started) {
                    throw std::runtime_error("head motion is unavailable");
                }
                bool yaw_motion_started = false;
                bool pitch_motion_started = false;
                if (servo_ok_) {
                    xSemaphoreTake(motion_mutex_, portMAX_DELAY);
                    yaw_motion_started = yaw_motion_.moving;
                    pitch_motion_started = pitch_motion_.moving;
                    xSemaphoreGive(motion_mutex_);
                }
                ESP_LOGI(TAG, "set_head_angles: yaw=%d (pos=%d) motion_started=%d, pitch=%d (pos=%d) motion_started=%d, uart=%d, servo_ok=%d",
                         yaw, yaw_pos, yaw_motion_started, pitch, pitch_pos, pitch_motion_started, (int)SERVO_UART_NUM, servo_ok_);
                cJSON* root = cJSON_CreateObject();
                cJSON_AddBoolToObject(root, "servo_init_ok", servo_ok_);
                cJSON_AddNumberToObject(root, "uart_num", (int)SERVO_UART_NUM);
                cJSON_AddNumberToObject(root, "yaw_pos", yaw_pos);
                cJSON_AddNumberToObject(root, "pitch_pos", pitch_pos);
                cJSON_AddNumberToObject(root, "yaw_motion_started", yaw_motion_started ? 1 : 0);
                cJSON_AddNumberToObject(root, "pitch_motion_started", pitch_motion_started ? 1 : 0);
                return root;
            });

        mcp_server.AddTool(
            "self.wifi.set_power_save",
            "Set the ESP32 WiFi power-save mode at runtime. Mode \"none\" disables modem sleep so high-rate command streams (for example, the pose-stream follower) avoid the ~800 ms TCP send jitter caused by the DTIM beacon cycle, at the cost of higher idle WiFi power consumption. Mode \"min_modem\" restores the xiaozhi-esp32 default light modem sleep for normal interactive use. Returns {ok, previous, current}.",
            PropertyList({Property("mode", kPropertyTypeString)}),
            [](const PropertyList& properties) -> ReturnValue {
                std::string mode_str = properties["mode"].value<std::string>();
                wifi_ps_type_t target;
                if (mode_str == "none") {
                    target = WIFI_PS_NONE;
                } else if (mode_str == "min_modem") {
                    target = WIFI_PS_MIN_MODEM;
                } else if (mode_str == "max_modem") {
                    target = WIFI_PS_MAX_MODEM;
                } else {
                    cJSON* root = cJSON_CreateObject();
                    cJSON_AddBoolToObject(root, "ok", false);
                    cJSON_AddStringToObject(root, "error", "invalid mode (expected 'none' | 'min_modem' | 'max_modem')");
                    return root;
                }

                auto ps_str = [](wifi_ps_type_t mode) -> const char* {
                    switch (mode) {
                    case WIFI_PS_NONE:
                        return "none";
                    case WIFI_PS_MIN_MODEM:
                        return "min_modem";
                    case WIFI_PS_MAX_MODEM:
                        return "max_modem";
                    default:
                        return "unknown";
                    }
                };

                wifi_ps_type_t previous = WIFI_PS_MIN_MODEM;
                esp_err_t get_result = esp_wifi_get_ps(&previous);
                esp_err_t set_result = esp_wifi_set_ps(target);
                const char* previous_str = get_result == ESP_OK ? ps_str(previous) : "unknown";

                ESP_LOGI(TAG, "wifi.set_power_save: target=%s previous=%s set_result=%d get_result=%d",
                         ps_str(target), previous_str, (int)set_result, (int)get_result);

                cJSON* root = cJSON_CreateObject();
                cJSON_AddBoolToObject(root, "ok", set_result == ESP_OK);
                cJSON_AddStringToObject(root, "previous", previous_str);
                cJSON_AddStringToObject(root, "current", set_result == ESP_OK ? ps_str(target) : ps_str(previous));
                if (set_result != ESP_OK) {
                    cJSON_AddNumberToObject(root, "esp_err", (int)set_result);
                }
                return root;
            });

        // Get current head angles
        mcp_server.AddTool(
            "self.robot.get_head_angles",
            "Get the current head angles (yaw, pitch) of the robot in degrees. "
            "Returns {\"yaw\":N,\"pitch\":N} on success; "
            "on persistent ReadPos failure returns "
            "{\"yaw\":null,\"pitch\":null,\"error\":...,\"servo_ok\":bool,"
            "\"yaw_attempts\":N,\"pitch_attempts\":N}.",
            PropertyList(),
            [this](const PropertyList& properties) -> ReturnValue {
                // Issue #123: retry ReadPos a few times before falling back
                // to an explicit error reply. Single-call ReadPos failures
                // (e.g. servo mid-motion, transient bus contention) are a
                // known transient mode that InitializeServo() already treats
                // as warning-and-continue; the previous behaviour of running
                // `ReadPos==-1` through the same `(pos-zero)*5/16` math as a
                // valid position produced sentinel `{-144,-194}` that was
                // indistinguishable from a genuine bus hang at the MCP layer
                // (see #1 / #100 / #118 hang judgments).
                constexpr int kReadPosRetryMax = 3;
                constexpr uint32_t kReadPosRetryDelayMs = 50;

                int yaw_pos = -1;
                int pitch_pos = -1;
                int yaw_attempts = 0;
                int pitch_attempts = 0;
                if (servo_ok_) {
                    xSemaphoreTake(scs_bus_mutex_, portMAX_DELAY);
                    for (int i = 0; i < kReadPosRetryMax; i++) {
                        yaw_attempts = i + 1;
                        yaw_pos = scs_bus_.ReadPos(SERVO_YAW_ID);
                        if (yaw_pos >= 0) break;
                        if (i + 1 < kReadPosRetryMax) {
                            vTaskDelay(pdMS_TO_TICKS(kReadPosRetryDelayMs));
                        }
                    }
                    for (int i = 0; i < kReadPosRetryMax; i++) {
                        pitch_attempts = i + 1;
                        pitch_pos = scs_bus_.ReadPos(SERVO_PITCH_ID);
                        if (pitch_pos >= 0) break;
                        if (i + 1 < kReadPosRetryMax) {
                            vTaskDelay(pdMS_TO_TICKS(kReadPosRetryDelayMs));
                        }
                    }
                    xSemaphoreGive(scs_bus_mutex_);
                }

                cJSON* root = cJSON_CreateObject();
                const bool yaw_ok = yaw_pos >= 0;
                const bool pitch_ok = pitch_pos >= 0;
                if (yaw_ok && pitch_ok) {
                    int yaw = (yaw_pos - 460) * 5 / 16;
                    int pitch = (pitch_pos - 620) * 5 / 16;
                    cJSON_AddNumberToObject(root, "yaw", yaw);
                    cJSON_AddNumberToObject(root, "pitch", pitch);
                } else {
                    cJSON_AddNullToObject(root, "yaw");
                    cJSON_AddNullToObject(root, "pitch");
                    char err[160];
                    snprintf(err, sizeof(err),
                             "ReadPos failed: yaw_raw=%d (attempts=%d) "
                             "pitch_raw=%d (attempts=%d) servo_ok=%d",
                             yaw_pos, yaw_attempts,
                             pitch_pos, pitch_attempts,
                             servo_ok_ ? 1 : 0);
                    cJSON_AddStringToObject(root, "error", err);
                    cJSON_AddBoolToObject(root, "servo_ok", servo_ok_);
                    cJSON_AddNumberToObject(root, "yaw_attempts", yaw_attempts);
                    cJSON_AddNumberToObject(root, "pitch_attempts", pitch_attempts);
                }
                char* str = cJSON_PrintUnformatted(root);
                std::string result(str);
                cJSON_free(str);
                cJSON_Delete(root);
                ESP_LOGI(TAG,
                         "get_head_angles: servo_ok=%d yaw_raw=%d (attempts=%d) "
                         "pitch_raw=%d (attempts=%d) result=%s",
                         servo_ok_ ? 1 : 0,
                         yaw_pos, yaw_attempts,
                         pitch_pos, pitch_attempts,
                         result.c_str());
                return result;
            });

        mcp_server.AddTool(
            "self.robot.set_servo_torque",
            "Enable or disable SCS0009 servo torque on the yaw / pitch axes "
            "independently. Disabling torque stops motor current on that axis; "
            "the head holds via static friction (no motion is commanded). "
            "On disable, the corresponding axis's MotionDriver state is reset "
            "(moving=false, position_unknown=true, request token invalidated) "
            "so a stale interpolation cannot resume on the bus and a "
            "subsequent same-target set_head_angles is re-dispatched rather "
            "than no-op-optimized. Re-enabling torque does NOT trigger a "
            "move -- the next set_head_angles or wobble call will. Returns "
            "the per-axis bus return codes. Diagnostic / power-management "
            "primitive; auto release on idle is tracked separately under "
            "#152 Phase 4.",
            PropertyList({Property("yaw_enabled", kPropertyTypeBoolean),
                          Property("pitch_enabled", kPropertyTypeBoolean)}),
            [this](const PropertyList& properties) -> ReturnValue {
                bool yaw_enabled = properties["yaw_enabled"].value<bool>();
                bool pitch_enabled = properties["pitch_enabled"].value<bool>();
                if (physical_motion_unavailable_.load(
                        std::memory_order_acquire) &&
                    (yaw_enabled || pitch_enabled)) {
                    throw std::runtime_error("head motion is unavailable");
                }
                PhysicalBehaviorOwner expected =
                    PhysicalBehaviorOwner::IDLE;
                if (!physical_behavior_owner_.compare_exchange_strong(
                        expected,
                        PhysicalBehaviorOwner::RAW,
                        std::memory_order_acq_rel)) {
                    throw std::runtime_error(
                        "another physical behavior is active");
                }
                ServoTorqueResult torque_result = InternalSetServoTorque(
                    yaw_enabled, pitch_enabled, ReleaseReason::kManual);
                physical_behavior_owner_.store(
                    PhysicalBehaviorOwner::IDLE,
                    std::memory_order_release);

                cJSON* root = cJSON_CreateObject();
                cJSON_AddBoolToObject(root, "yaw_enabled", yaw_enabled);
                cJSON_AddBoolToObject(root, "pitch_enabled", pitch_enabled);
                cJSON_AddNumberToObject(root, "yaw_bus_return",
                                        torque_result.yaw_bus_return);
                cJSON_AddNumberToObject(root, "pitch_bus_return",
                                        torque_result.pitch_bus_return);
                cJSON_AddBoolToObject(root, "servo_ok", servo_ok_);
                // Issue #171: ok counts an idempotent no-op as success but a
                // wait-budget exhaustion as failure (the requested torque
                // transition did not actually happen on the bus).
                cJSON_AddBoolToObject(
                    root, "ok",
                    servo_ok_ && (torque_result.idempotent_short_circuit ||
                                  (torque_result.yaw_ok &&
                                   torque_result.pitch_ok)));
                // Issue #171: the old single `short_circuited` field is
                // removed (no alias). These two orthogonal, mutually
                // exclusive flags let callers distinguish a degraded-bus
                // wait-exhaustion from an idempotent no-op success.
                cJSON_AddBoolToObject(root, "idempotent_short_circuit",
                                      torque_result.idempotent_short_circuit);
                cJSON_AddBoolToObject(root, "wait_exhausted",
                                      torque_result.wait_exhausted);
                if (!servo_ok_) {
                    cJSON_AddStringToObject(root, "error",
                                            "Servo bus not initialized.");
                }
                return root;
            });

        mcp_server.AddTool(
            "self.robot.set_auto_torque_release",
            "Enable or disable automatic SCS0009 torque release after "
            "motion idle timeout. timeout_ms is clamped by the firmware "
            "to 500..600000 ms. Disabling this setting does not re-enable "
            "torque if it is already released; the next set_head_angles, "
            "wobble, or explicit set_servo_torque(true, true) call "
            "re-engages torque.",
            PropertyList({Property("enabled", kPropertyTypeBoolean),
                          Property("timeout_ms", kPropertyTypeInteger,
                                   (int)AUTO_TORQUE_RELEASE_DEFAULT_MS)}),
            [this](const PropertyList& properties) -> ReturnValue {
                bool enabled = properties["enabled"].value<bool>();
                int requested_timeout_ms = properties["timeout_ms"].value<int>();
                bool clamped = false;
                uint32_t timeout_ms = 0;

                if (requested_timeout_ms <
                    static_cast<int>(AUTO_TORQUE_RELEASE_MIN_MS)) {
                    timeout_ms = AUTO_TORQUE_RELEASE_MIN_MS;
                    clamped = true;
                    ESP_LOGW(TAG,
                             "set_auto_torque_release: timeout_ms=%d below "
                             "minimum %u, clamping",
                             requested_timeout_ms,
                             (unsigned)AUTO_TORQUE_RELEASE_MIN_MS);
                } else if (requested_timeout_ms >
                           static_cast<int>(AUTO_TORQUE_RELEASE_MAX_MS)) {
                    timeout_ms = AUTO_TORQUE_RELEASE_MAX_MS;
                    clamped = true;
                    ESP_LOGW(TAG,
                             "set_auto_torque_release: timeout_ms=%d above "
                             "maximum %u, clamping",
                             requested_timeout_ms,
                             (unsigned)AUTO_TORQUE_RELEASE_MAX_MS);
                } else {
                    timeout_ms = static_cast<uint32_t>(requested_timeout_ms);
                }

                bool torque_released_at_call =
                    torque_state_.load(std::memory_order_acquire) ==
                    TorqueState::kReleased;
                auto_release_timeout_ms_.store(timeout_ms,
                                               std::memory_order_release);
                auto_release_enabled_.store(enabled,
                                            std::memory_order_release);

                ESP_LOGI(TAG,
                         "set_auto_torque_release: enabled=%d "
                         "timeout_ms=%u clamped=%d "
                         "torque_released_at_call=%d",
                         enabled ? 1 : 0, (unsigned)timeout_ms,
                         clamped ? 1 : 0,
                         torque_released_at_call ? 1 : 0);

                cJSON* root = cJSON_CreateObject();
                cJSON_AddBoolToObject(root, "enabled", enabled);
                cJSON_AddNumberToObject(root, "timeout_ms", timeout_ms);
                cJSON_AddBoolToObject(root, "clamped", clamped);
                cJSON_AddBoolToObject(root, "torque_released_at_call",
                                      torque_released_at_call);
                return root;
            });

        // Diagnostic: toggle GPIO6 (servo TX) HIGH/LOW to verify physical signal
        mcp_server.AddTool(
            "self.robot.gpio_test",
            "Diagnostic: toggle GPIO6 (servo TX pin) HIGH/LOW 5 times at 100ms intervals to verify physical signal output. Restores UART pins after.",
            PropertyList(),
            [this](const PropertyList& properties) -> ReturnValue {
                if (!servo_ok_ || scs_bus_mutex_ == nullptr) {
                    throw std::runtime_error("servo bus is unavailable");
                }
                if (physical_motion_unavailable_.load(
                        std::memory_order_acquire)) {
                    throw std::runtime_error("head motion is unavailable");
                }
                PhysicalBehaviorOwner expected = PhysicalBehaviorOwner::IDLE;
                if (!physical_behavior_owner_.compare_exchange_strong(
                        expected,
                        PhysicalBehaviorOwner::RAW,
                        std::memory_order_acq_rel)) {
                    throw std::runtime_error(
                        "another physical behavior is active");
                }
                cJSON* root = cJSON_CreateObject();

                gpio_num_t pin = static_cast<gpio_num_t>(SERVO_TX_PIN);

                xSemaphoreTake(scs_bus_mutex_, portMAX_DELAY);
                esp_err_t err_dir = gpio_set_direction(pin, GPIO_MODE_OUTPUT);
                cJSON_AddStringToObject(root, "set_direction", esp_err_to_name(err_dir));
                cJSON_AddNumberToObject(root, "pin", SERVO_TX_PIN);

                cJSON* toggles = cJSON_CreateArray();
                for (int i = 0; i < 5; i++) {
                    esp_err_t err_h = gpio_set_level(pin, 1);
                    vTaskDelay(pdMS_TO_TICKS(100));
                    esp_err_t err_l = gpio_set_level(pin, 0);
                    vTaskDelay(pdMS_TO_TICKS(100));
                    cJSON* item = cJSON_CreateObject();
                    cJSON_AddNumberToObject(item, "iter", i);
                    cJSON_AddStringToObject(item, "high", esp_err_to_name(err_h));
                    cJSON_AddStringToObject(item, "low", esp_err_to_name(err_l));
                    cJSON_AddItemToArray(toggles, item);
                }
                cJSON_AddItemToObject(root, "toggles", toggles);

                // Restore UART pin assignment after raw GPIO toggling
                esp_err_t err_restore = uart_set_pin(SERVO_UART_NUM, SERVO_TX_PIN, SERVO_RX_PIN,
                                                    UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE);
                xSemaphoreGive(scs_bus_mutex_);
                physical_behavior_owner_.store(
                    PhysicalBehaviorOwner::IDLE,
                    std::memory_order_release);
                cJSON_AddStringToObject(root, "uart_pin_restore", esp_err_to_name(err_restore));

                char* str = cJSON_PrintUnformatted(root);
                std::string result(str);
                cJSON_free(str);
                cJSON_Delete(root);
                ESP_LOGI(TAG, "gpio_test: %s", result.c_str());
                return result;
            });

        // Diagnostic: send raw bytes via uart_write_bytes, equivalent to WritePos(1, 1000, 0, 0)
        mcp_server.AddTool(
            "self.robot.uart_diag",
            "Diagnostic: send raw 8 bytes (FF FF 01 04 03 E8 00 00) directly via uart_write_bytes. Returns sent byte count and rx buffer length before/after.",
            PropertyList(),
            [this](const PropertyList& properties) -> ReturnValue {
                if (physical_motion_unavailable_.load(
                        std::memory_order_acquire)) {
                    throw std::runtime_error("head motion is unavailable");
                }
                PhysicalBehaviorOwner expected = PhysicalBehaviorOwner::IDLE;
                if (!physical_behavior_owner_.compare_exchange_strong(
                        expected,
                        PhysicalBehaviorOwner::RAW,
                        std::memory_order_acq_rel)) {
                    throw std::runtime_error(
                        "another physical behavior is active");
                }
                cJSON* root = cJSON_CreateObject();

                size_t buf_before = 0;
                esp_err_t err_b = ESP_ERR_INVALID_STATE;
                int written = -1;
                esp_err_t err_wait = ESP_ERR_INVALID_STATE;
                esp_err_t err_a = ESP_ERR_INVALID_STATE;
                size_t buf_after = 0;
                const uint8_t bytes[] = {0xFF, 0xFF, 0x01, 0x04, 0x03, 0xE8, 0x00, 0x00};

                if (servo_ok_) {
                    xSemaphoreTake(scs_bus_mutex_, portMAX_DELAY);

                    err_b = uart_get_buffered_data_len(SERVO_UART_NUM, &buf_before);

                    written = uart_write_bytes(SERVO_UART_NUM, (const char*)bytes, sizeof(bytes));

                    // Wait for TX FIFO drain
                    err_wait = uart_wait_tx_done(SERVO_UART_NUM, pdMS_TO_TICKS(100));

                    vTaskDelay(pdMS_TO_TICKS(20));

                    err_a = uart_get_buffered_data_len(SERVO_UART_NUM, &buf_after);

                    xSemaphoreGive(scs_bus_mutex_);
                }
                physical_behavior_owner_.store(
                    PhysicalBehaviorOwner::IDLE,
                    std::memory_order_release);
                cJSON_AddStringToObject(root, "buf_before_status", esp_err_to_name(err_b));
                cJSON_AddNumberToObject(root, "buf_before", buf_before);

                cJSON_AddNumberToObject(root, "written", written);
                cJSON_AddNumberToObject(root, "expected", (int)sizeof(bytes));

                cJSON_AddStringToObject(root, "tx_done_status", esp_err_to_name(err_wait));

                cJSON_AddStringToObject(root, "buf_after_status", esp_err_to_name(err_a));
                cJSON_AddNumberToObject(root, "buf_after", buf_after);

                char* str = cJSON_PrintUnformatted(root);
                std::string result(str);
                cJSON_free(str);
                cJSON_Delete(root);
                ESP_LOGI(TAG, "uart_diag: %s", result.c_str());
                return result;
            });

        // Diagnostic: read PY32 REG_GPIO_O_L (output low byte) and report
        // whether VM EN (pin 0) is HIGH. Used to investigate "servo stops
        // moving after the first move_head" — if VM EN drops to LOW under
        // load, the servo loses power even though the I2C write succeeds.
        mcp_server.AddTool(
            "self.robot.check_vm_en",
            "Diagnostic: read PY32 REG_GPIO_O_L and report whether VM EN (pin 0 = servo power) is currently HIGH. "
            "Returns {io_expander_present, i2c_read_ok, raw, vm_en_high}.",
            PropertyList(),
            [this](const PropertyList&) -> ReturnValue {
                cJSON* root = cJSON_CreateObject();
                bool present = (io_expander_ != nullptr);
                cJSON_AddBoolToObject(root, "io_expander_present", present);
                if (present) {
                    uint8_t out_low = 0;
                    bool ok = io_expander_->ReadOutputLow(&out_low);
                    cJSON_AddBoolToObject(root, "i2c_read_ok", ok);
                    if (ok) {
                        cJSON_AddNumberToObject(root, "raw", out_low);
                        cJSON_AddBoolToObject(root, "vm_en_high", (out_low & 0x01) != 0);
                    }
                }
                ESP_LOGI(TAG, "check_vm_en queried");
                return root;
            });

        mcp_server.AddTool(
            "self.display.set_offer_pending",
            "Set whether one prepared offer is waiting for head-touch "
            "acknowledgment. The idle screensaver stays hidden while true.",
            PropertyList({Property("pending", kPropertyTypeBoolean)}),
            [this](const PropertyList& properties) -> ReturnValue {
                bool pending = properties["pending"].value<bool>();
                SetOfferPending(pending);
                cJSON* root = cJSON_CreateObject();
                cJSON_AddBoolToObject(root, "ok", true);
                cJSON_AddBoolToObject(root, "pending", pending);
                return root;
            });

        mcp_server.AddTool(
            "self.display.set_weather",
            "Update the idle screensaver with a QWeather icon code, "
            "whole-degree Celsius temperature, and the provider's Chinese "
            "condition text.",
            PropertyList({
                Property("icon_code", kPropertyTypeInteger, 100, 100, 999),
                Property("temperature_c", kPropertyTypeInteger, 0, -99, 99),
                Property("summary", kPropertyTypeString),
            }),
            [this](const PropertyList& properties) -> ReturnValue {
                int icon_code = properties["icon_code"].value<int>();
                int temperature_c =
                    properties["temperature_c"].value<int>();
                std::string summary =
                    properties["summary"].value<std::string>();
                if (summary.empty() || summary.size() > 48) {
                    throw std::invalid_argument(
                        "summary must contain 1 to 48 UTF-8 bytes");
                }
                SetScreenSaverWeather(
                    icon_code, temperature_c, summary);
                cJSON* root = cJSON_CreateObject();
                cJSON_AddBoolToObject(root, "ok", true);
                cJSON_AddNumberToObject(root, "icon_code", icon_code);
                cJSON_AddNumberToObject(
                    root, "temperature_c", temperature_c);
                cJSON_AddStringToObject(root, "summary", summary.c_str());
                return root;
            });

        mcp_server.AddTool(
            "self.location.get",
            "Return cached approximate coordinates for the robot's current "
            "Wi-Fi network. Returns an empty object while unavailable.",
            PropertyList(),
            [this](const PropertyList&) -> ReturnValue {
                return GetPublicIpLocation();
            });

        mcp_server.AddTool(
            "self.display.set_clock",
            "Set the current Unix time for the China-local idle clock.",
            PropertyList({Property(
                "epoch_seconds", kPropertyTypeInteger,
                1700000000, 2147483647)}),
            [this](const PropertyList& properties) -> ReturnValue {
                int epoch_seconds =
                    properties["epoch_seconds"].value<int>();
                SetScreenSaverClock(epoch_seconds);
                cJSON* root = cJSON_CreateObject();
                cJSON_AddBoolToObject(root, "ok", true);
                return root;
            });


        // Phase 7: head-touch (Si12T). Returns the latest debounced zone
        // states plus the most recent gesture event. Polled by the MCP client
        // to notice TAP/STROKE on the head without holding open a stream.
        mcp_server.AddTool(
            "self.touch.get_touch_state",
            "Get the current head-touch sensor state and last gesture event "
            "(tap/stroke/idle) with its age in milliseconds.",
            PropertyList(),
            [this](const PropertyList& properties) -> ReturnValue {
                cJSON* root = cJSON_CreateObject();
                cJSON_AddBoolToObject(root, "available", si12t_ok_);
                cJSON_AddBoolToObject(root, "zone0", last_zone_snapshot_[0]);
                cJSON_AddBoolToObject(root, "zone1", last_zone_snapshot_[1]);
                cJSON_AddBoolToObject(root, "zone2", last_zone_snapshot_[2]);
                cJSON_AddNumberToObject(root, "raw", last_output1_raw_);
                const char* ev = "idle";
                switch (last_event_) {
                    case TouchEvent::TAP:    ev = "tap";    break;
                    case TouchEvent::STROKE: ev = "stroke"; break;
                    case TouchEvent::IDLE:
                    default:                 ev = "idle";   break;
                }
                cJSON_AddStringToObject(root, "last_event", ev);
                int64_t age_ms = -1;
                if (last_event_us_ != 0) {
                    int64_t now_us = (int64_t)esp_timer_get_time();
                    age_ms = (now_us - (int64_t)last_event_us_) / 1000;
                    if (age_ms < 0) age_ms = 0;
                }
                cJSON_AddNumberToObject(root, "last_event_age_ms", (double)age_ms);
                return root;
            });

        // ---- LED tools (12x WS2812C on the StackChan base) ----
        // The strip is driven by the PY32 IO expander on its pin 13, not by
        // an ESP32 GPIO. Updates are non-latching writes into the PY32 LED
        // RAM followed by a single RefreshLeds() to strobe the strip. All
        // four tools refresh implicitly so the LLM gets WYSIWYG behaviour.
        mcp_server.AddTool(
            "self.led.set_color",
            "Set a single RGB LED on the StackChan base. There are 12 LEDs "
            "(index 0..11). r/g/b are 0..255. Updates immediately.",
            PropertyList({
                Property("index", kPropertyTypeInteger, 0, RGB_LED_COUNT - 1),
                Property("r", kPropertyTypeInteger, 0, 255),
                Property("g", kPropertyTypeInteger, 0, 255),
                Property("b", kPropertyTypeInteger, 0, 255),
            }),
            [this](const PropertyList& properties) -> ReturnValue {
                cJSON* root = cJSON_CreateObject();
                cJSON_AddBoolToObject(root, "available", rgb_ok_);
                if (!rgb_ok_) {
                    cJSON_AddStringToObject(root, "error", "RGB strip not available (PY32 init failed?)");
                    return root;
                }
                int index = properties["index"].value<int>();
                uint8_t r = ClampByte(properties["r"].value<int>());
                uint8_t g = ClampByte(properties["g"].value<int>());
                uint8_t b = ClampByte(properties["b"].value<int>());
                bool ok_w = io_expander_->SetLedColor((uint8_t)index, r, g, b);
                bool ok_r = ok_w ? io_expander_->RefreshLeds() : false;
                cJSON_AddBoolToObject(root, "ok", ok_w && ok_r);
                cJSON_AddNumberToObject(root, "index", index);
                ESP_LOGI(TAG, "set_led: index=%d rgb=(%u,%u,%u) ok=%d", index, r, g, b, ok_w && ok_r);
                return root;
            });

        mcp_server.AddTool(
            "self.led.set_all",
            "Set all 12 RGB LEDs on the StackChan base to the same color. "
            "r/g/b are 0..255. Updates immediately.",
            PropertyList({
                Property("r", kPropertyTypeInteger, 0, 255),
                Property("g", kPropertyTypeInteger, 0, 255),
                Property("b", kPropertyTypeInteger, 0, 255),
            }),
            [this](const PropertyList& properties) -> ReturnValue {
                cJSON* root = cJSON_CreateObject();
                cJSON_AddBoolToObject(root, "available", rgb_ok_);
                if (!rgb_ok_) {
                    cJSON_AddStringToObject(root, "error", "RGB strip not available (PY32 init failed?)");
                    return root;
                }
                uint8_t r = ClampByte(properties["r"].value<int>());
                uint8_t g = ClampByte(properties["g"].value<int>());
                uint8_t b = ClampByte(properties["b"].value<int>());
                uint8_t buf[RGB_LED_COUNT * 2];
                uint8_t pair[2];
                PackRgb565(r, g, b, pair);
                for (int i = 0; i < RGB_LED_COUNT; i++) {
                    buf[i * 2 + 0] = pair[0];
                    buf[i * 2 + 1] = pair[1];
                }
                bool ok_w = io_expander_->SetLedData(buf, sizeof(buf));
                bool ok_r = ok_w ? io_expander_->RefreshLeds() : false;
                cJSON_AddBoolToObject(root, "ok", ok_w && ok_r);
                ESP_LOGI(TAG, "set_all_leds: rgb=(%u,%u,%u) ok=%d", r, g, b, ok_w && ok_r);
                return root;
            });

        // Batch set: accepts a JSON-encoded array of 12 [r,g,b] triples.
        // Single I2C burst + one refresh — use this for animations or any
        // multi-color pattern to avoid 12x round-trips. Missing trailing
        // entries are left at their previous color (PY32 RAM is sticky).
        mcp_server.AddTool(
            "self.led.set_many",
            "Set multiple RGB LEDs in one shot. 'colors' is a JSON-encoded "
            "array of [r,g,b] triples starting at index 0, e.g. "
            "\"[[255,0,0],[0,255,0],[0,0,255]]\". Up to 12 entries; extras "
            "are ignored, missing entries keep their previous color. "
            "r/g/b are 0..255. Updates immediately.",
            PropertyList({Property("colors", kPropertyTypeString)}),
            [this](const PropertyList& properties) -> ReturnValue {
                cJSON* root = cJSON_CreateObject();
                cJSON_AddBoolToObject(root, "available", rgb_ok_);
                if (!rgb_ok_) {
                    cJSON_AddStringToObject(root, "error", "RGB strip not available (PY32 init failed?)");
                    return root;
                }
                std::string json = properties["colors"].value<std::string>();
                cJSON* arr = cJSON_Parse(json.c_str());
                if (arr == nullptr || !cJSON_IsArray(arr)) {
                    cJSON_AddBoolToObject(root, "ok", false);
                    cJSON_AddStringToObject(root, "error",
                        "colors must be a JSON array of [r,g,b] triples");
                    if (arr != nullptr) cJSON_Delete(arr);
                    return root;
                }
                int n = cJSON_GetArraySize(arr);
                if (n > RGB_LED_COUNT) n = RGB_LED_COUNT;

                // Validate every entry FIRST and pack into a local buffer.
                // Only after the whole array is known good do we touch the
                // PY32 — that way a malformed entry at i=5 cannot leave
                // LEDs 0..4 mutated (atomic semantics, same as
                // other batched handlers). cJSON_IsNumber is required because
                // valueint silently returns 0 for non-number nodes (string,
                // null, bool), so without the guard a payload like
                // [["255",0,0]] would write black and report ok=true.
                uint8_t buf[RGB_LED_COUNT * 2];   // 24 bytes, fits the cap
                bool parse_ok = true;
                for (int i = 0; i < n; i++) {
                    cJSON* triple = cJSON_GetArrayItem(arr, i);
                    if (!cJSON_IsArray(triple) || cJSON_GetArraySize(triple) < 3) {
                        parse_ok = false;
                        break;
                    }
                    cJSON* jr = cJSON_GetArrayItem(triple, 0);
                    cJSON* jg = cJSON_GetArrayItem(triple, 1);
                    cJSON* jb = cJSON_GetArrayItem(triple, 2);
                    if (!cJSON_IsNumber(jr) || !cJSON_IsNumber(jg) || !cJSON_IsNumber(jb)) {
                        parse_ok = false;
                        break;
                    }
                    PackRgb565(ClampByte(jr->valueint),
                               ClampByte(jg->valueint),
                               ClampByte(jb->valueint),
                               &buf[i * 2]);
                }
                cJSON_Delete(arr);

                // Single I2C burst for the validated prefix, then one latch.
                // n=0 is treated as success (gateway schema enforces
                // minItems=1, but a direct device caller could hit this).
                bool ok_w = false, ok_r = false;
                if (parse_ok && n > 0) {
                    ok_w = io_expander_->SetLedData(buf, (size_t)(n * 2));
                    ok_r = ok_w ? io_expander_->RefreshLeds() : false;
                }
                bool ok = parse_ok && (n == 0 || (ok_w && ok_r));
                cJSON_AddBoolToObject(root, "ok", ok);
                cJSON_AddNumberToObject(root, "written", ok ? n : 0);
                if (!parse_ok) {
                    cJSON_AddStringToObject(root, "error",
                        "Each entry must be a [r,g,b] triple of integers");
                }
                ESP_LOGI(TAG, "set_many_leds: written=%d/%d ok=%d",
                         ok ? n : 0, n, ok);
                return root;
            });

        mcp_server.AddTool(
            "self.led.clear",
            "Turn off all 12 RGB LEDs on the StackChan base. Updates immediately.",
            PropertyList(),
            [this](const PropertyList&) -> ReturnValue {
                cJSON* root = cJSON_CreateObject();
                cJSON_AddBoolToObject(root, "available", rgb_ok_);
                if (!rgb_ok_) {
                    cJSON_AddStringToObject(root, "error", "RGB strip not available (PY32 init failed?)");
                    return root;
                }
                uint8_t buf[RGB_LED_COUNT * 2] = {0};
                bool ok_w = io_expander_->SetLedData(buf, sizeof(buf));
                bool ok_r = ok_w ? io_expander_->RefreshLeds() : false;
                cJSON_AddBoolToObject(root, "ok", ok_w && ok_r);
                ESP_LOGI(TAG, "clear_leds: ok=%d", ok_w && ok_r);
                return root;
            });

        // ---- Generic I2C bus tools (Grove Port A) ----
        // Expose the external Port A I2C bus to the MCP client so that
        // attached M5Stack Unit modules (ENV III, ToF, gas sensor, PaHub,
        // etc.) can be driven from the gateway / host side without
        // recompiling and re-flashing per Unit. The on-board IC bus (PMIC,
        // touch, IMU, AW9523, audio codec) is on a physically separate I2C
        // controller and is NOT reachable from these tools by construction.

        mcp_server.AddTool(
            "self.i2c.scan",
            "Scan the external I2C bus on Grove Port A and return all 7-bit "
            "addresses (probe range 0x08..0x77, excluding I2C reserved "
            "ranges) that ACK a probe. Use this to discover attached "
            "M5Stack Unit modules (ENV III, ToF, gas sensor, PaHub, etc.). "
            "On-board ICs on the internal bus are NOT included (this tool "
            "operates on a physically separate bus). Returns "
            "{\"ok\":true, \"addresses\":[...]}.",
            PropertyList(),
            [this](const PropertyList&) -> ReturnValue {
                cJSON* root = cJSON_CreateObject();
                cJSON* addrs = cJSON_CreateArray();
                int found = 0;
                // Probe 0x08..0x77 (skip I2C reserved 0x00-0x07 / 0x78-0x7F).
                // 200 ms per-probe timeout matches the boot-time I2cDetect()
                // and reliably catches slower Units (RCWL-9620 etc.).
                for (uint8_t addr = 0x08; addr < 0x78; addr++) {
                    esp_err_t ret = i2c_master_probe(port_a_i2c_bus_, addr, pdMS_TO_TICKS(200));
                    if (ret == ESP_OK) {
                        cJSON_AddItemToArray(addrs, cJSON_CreateNumber(addr));
                        found++;
                    }
                }
                cJSON_AddBoolToObject(root, "ok", true);
                cJSON_AddItemToObject(root, "addresses", addrs);
                ESP_LOGI(TAG, "i2c.scan: found %d device(s) on Port A", found);
                return root;
            });

        mcp_server.AddTool(
            "self.i2c.read",
            "Read n_bytes from an I2C device at 7-bit address `addr` on Grove "
            "Port A. `addr` is restricted to 0x08..0x77 (I2C reserved ranges "
            "excluded — matches the self.i2c.scan probe range). Use this for "
            "protocols that read the device's current register / output "
            "without a preceding write (e.g. sensors that latch a measurement "
            "from a prior command). For typical 'write register address, "
            "then read' patterns, use self.i2c.write_read instead. Returns "
            "{\"ok\":true, \"bytes\":[...]} or "
            "{\"ok\":false, \"error\":\"ESP_ERR_TIMEOUT\"} on NACK. Optional "
            "`scl_speed_hz` (default 400000) sets the I2C clock for this "
            "transaction; lower it (e.g. 200000) for slower Units such as the "
            "RCWL-9620 ultrasonic ranger that fail at 400 kHz with "
            "ESP_ERR_INVALID_STATE.",
            PropertyList({
                Property("addr", kPropertyTypeInteger, 0x08, 0x77),
                Property("n_bytes", kPropertyTypeInteger, 1, 256),
                Property("scl_speed_hz", kPropertyTypeInteger, 400000, 100000, 1000000)
            }),
            [this](const PropertyList& props) -> ReturnValue {
                cJSON* root = cJSON_CreateObject();
                uint8_t addr = static_cast<uint8_t>(props["addr"].value<int>());
                int n = props["n_bytes"].value<int>();

                i2c_device_config_t cfg = {
                    .dev_addr_length = I2C_ADDR_BIT_LEN_7,
                    .device_address = addr,
                    .scl_speed_hz = static_cast<uint32_t>(props["scl_speed_hz"].value<int>()),
                };
                i2c_master_dev_handle_t dev;
                esp_err_t err = i2c_master_bus_add_device(port_a_i2c_bus_, &cfg, &dev);
                if (err != ESP_OK) {
                    cJSON_AddBoolToObject(root, "ok", false);
                    cJSON_AddStringToObject(root, "error", esp_err_to_name(err));
                    ESP_LOGW(TAG, "i2c.read addr=0x%02X add_device failed: %s",
                             addr, esp_err_to_name(err));
                    return root;
                }

                std::vector<uint8_t> buf(static_cast<size_t>(n));
                err = i2c_master_receive(dev, buf.data(), buf.size(), 100);
                i2c_master_bus_rm_device(dev);

                if (err == ESP_OK) {
                    cJSON* bytes = cJSON_CreateArray();
                    for (uint8_t b : buf) {
                        cJSON_AddItemToArray(bytes, cJSON_CreateNumber(b));
                    }
                    cJSON_AddBoolToObject(root, "ok", true);
                    cJSON_AddItemToObject(root, "bytes", bytes);
                } else {
                    cJSON_AddBoolToObject(root, "ok", false);
                    cJSON_AddStringToObject(root, "error", esp_err_to_name(err));
                }
                ESP_LOGI(TAG, "i2c.read addr=0x%02X n=%d ok=%d",
                         addr, n, err == ESP_OK);
                return root;
            });

        Property i2c_write_bytes_prop(
            "bytes", kPropertyTypeArray, kPropertyElementTypeInteger, 0, 255
        );
        i2c_write_bytes_prop.set_max_items(256);  // 対称: n_bytes の read 上限と同じ
        mcp_server.AddTool(
            "self.i2c.write",
            "Write bytes to an I2C device at 7-bit address `addr` on Grove "
            "Port A. `addr` is restricted to 0x08..0x77 (I2C reserved ranges "
            "excluded — General-call address 0x00 etc. cannot accidentally "
            "broadcast-write to all attached Units). `bytes` is an array of "
            "integers (0..255, max 256 items). This tool operates on the "
            "external Port A bus only; on-board ICs (PMIC, AW9523, touch, "
            "etc.) on the internal bus are not reachable. Returns "
            "{\"ok\":true} on ACK or "
            "{\"ok\":false, \"error\":\"ESP_ERR_TIMEOUT\"} on NACK. Optional "
            "`scl_speed_hz` (default 400000) sets the I2C clock for this "
            "transaction; lower it (e.g. 200000) for slower Units such as the "
            "RCWL-9620 ultrasonic ranger that fail at 400 kHz with "
            "ESP_ERR_INVALID_STATE.",
            PropertyList({
                Property("addr", kPropertyTypeInteger, 0x08, 0x77),
                i2c_write_bytes_prop,
                Property("scl_speed_hz", kPropertyTypeInteger, 400000, 100000, 1000000)
            }),
            [this](const PropertyList& props) -> ReturnValue {
                cJSON* root = cJSON_CreateObject();
                uint8_t addr = static_cast<uint8_t>(props["addr"].value<int>());
                auto bytes_int = props["bytes"].value<std::vector<int>>();

                i2c_device_config_t cfg = {
                    .dev_addr_length = I2C_ADDR_BIT_LEN_7,
                    .device_address = addr,
                    .scl_speed_hz = static_cast<uint32_t>(props["scl_speed_hz"].value<int>()),
                };
                i2c_master_dev_handle_t dev;
                esp_err_t err = i2c_master_bus_add_device(port_a_i2c_bus_, &cfg, &dev);
                if (err != ESP_OK) {
                    cJSON_AddBoolToObject(root, "ok", false);
                    cJSON_AddStringToObject(root, "error", esp_err_to_name(err));
                    ESP_LOGW(TAG, "i2c.write addr=0x%02X add_device failed: %s",
                             addr, esp_err_to_name(err));
                    return root;
                }

                std::vector<uint8_t> buf;
                buf.reserve(bytes_int.size());
                for (int b : bytes_int) buf.push_back(static_cast<uint8_t>(b));

                err = i2c_master_transmit(dev, buf.data(), buf.size(), 100);
                i2c_master_bus_rm_device(dev);

                if (err == ESP_OK) {
                    cJSON_AddBoolToObject(root, "ok", true);
                } else {
                    cJSON_AddBoolToObject(root, "ok", false);
                    cJSON_AddStringToObject(root, "error", esp_err_to_name(err));
                }
                ESP_LOGI(TAG, "i2c.write addr=0x%02X n=%d ok=%d",
                         addr, (int)buf.size(), err == ESP_OK);
                return root;
            });

        Property i2c_wr_write_bytes_prop(
            "write_bytes", kPropertyTypeArray, kPropertyElementTypeInteger, 0, 255
        );
        i2c_wr_write_bytes_prop.set_max_items(256);  // 対称: n_bytes の read 上限と同じ
        mcp_server.AddTool(
            "self.i2c.write_read",
            "Write `write_bytes` to an I2C device at 7-bit address `addr` on "
            "Grove Port A, then read n_bytes back in a single transaction "
            "(Repeated Start). `addr` is restricted to 0x08..0x77 (I2C "
            "reserved ranges excluded). `write_bytes` is an array of "
            "integers (0..255, max 256 items). This is the common 'set "
            "register pointer, then read' pattern: pass write_bytes=[reg_addr] "
            "to read from a specific register. Returns "
            "{\"ok\":true, \"bytes\":[...]} or "
            "{\"ok\":false, \"error\":\"...\"} on failure. Optional "
            "`scl_speed_hz` (default 400000) sets the I2C clock for this "
            "transaction; lower it (e.g. 200000) for slower Units such as the "
            "RCWL-9620 ultrasonic ranger that fail at 400 kHz with "
            "ESP_ERR_INVALID_STATE.",
            PropertyList({
                Property("addr", kPropertyTypeInteger, 0x08, 0x77),
                i2c_wr_write_bytes_prop,
                Property("n_bytes", kPropertyTypeInteger, 1, 256),
                Property("scl_speed_hz", kPropertyTypeInteger, 400000, 100000, 1000000)
            }),
            [this](const PropertyList& props) -> ReturnValue {
                cJSON* root = cJSON_CreateObject();
                uint8_t addr = static_cast<uint8_t>(props["addr"].value<int>());
                auto write_bytes_int = props["write_bytes"].value<std::vector<int>>();
                int n = props["n_bytes"].value<int>();

                i2c_device_config_t cfg = {
                    .dev_addr_length = I2C_ADDR_BIT_LEN_7,
                    .device_address = addr,
                    .scl_speed_hz = static_cast<uint32_t>(props["scl_speed_hz"].value<int>()),
                };
                i2c_master_dev_handle_t dev;
                esp_err_t err = i2c_master_bus_add_device(port_a_i2c_bus_, &cfg, &dev);
                if (err != ESP_OK) {
                    cJSON_AddBoolToObject(root, "ok", false);
                    cJSON_AddStringToObject(root, "error", esp_err_to_name(err));
                    ESP_LOGW(TAG, "i2c.write_read addr=0x%02X add_device failed: %s",
                             addr, esp_err_to_name(err));
                    return root;
                }

                std::vector<uint8_t> write_buf;
                write_buf.reserve(write_bytes_int.size());
                for (int b : write_bytes_int) write_buf.push_back(static_cast<uint8_t>(b));

                std::vector<uint8_t> read_buf(static_cast<size_t>(n));
                err = i2c_master_transmit_receive(dev,
                                                   write_buf.data(), write_buf.size(),
                                                   read_buf.data(), read_buf.size(),
                                                   100);
                i2c_master_bus_rm_device(dev);

                if (err == ESP_OK) {
                    cJSON* bytes = cJSON_CreateArray();
                    for (uint8_t b : read_buf) {
                        cJSON_AddItemToArray(bytes, cJSON_CreateNumber(b));
                    }
                    cJSON_AddBoolToObject(root, "ok", true);
                    cJSON_AddItemToObject(root, "bytes", bytes);
                } else {
                    cJSON_AddBoolToObject(root, "ok", false);
                    cJSON_AddStringToObject(root, "error", esp_err_to_name(err));
                }
                ESP_LOGI(TAG, "i2c.write_read addr=0x%02X w=%d r=%d ok=%d",
                         addr, (int)write_buf.size(), n, err == ESP_OK);
                return root;
            });

        // ---- Generic Port B WS2812 strip tools ----
        // Expose the CoreS3 Port B digital output (GPIO 9) as a generic
        // WS2812-compatible strip driver. This is independent from self.led.*,
        // which drives the 12-LED base strip through the PY32 I2C path.

        mcp_server.AddTool(
            "self.port_b.ws2812.init",
            "Initialize a WS2812-compatible LED strip connected to Port B "
            "(CoreS3 HY2.0-4P digital OUTPUT, GPIO 9). led_count is the "
            "number of LEDs in the strip (1..256). This allocates the "
            "ESP-IDF led_strip RMT backend and must succeed before calling "
            "self.port_b.ws2812.set_pixel, set_strip, refresh, or clear. "
            "Repeated calls with the same led_count are no-ops; a different "
            "led_count tears down and rebuilds the strip handle. Returns "
            "{\"available\":true,\"ok\":true,\"led_count\":N} on success or "
            "{\"available\":false,\"ok\":false,\"led_count\":N,"
            "\"error\":\"ESP_ERR_...\"} on failure. The strip protocol is "
            "3.3 V CMOS data on GPIO 9; most modern WS2812B-V5/B2 strips "
            "tolerate this, while older strict 5 V V_IH variants may need "
            "an external level shifter.",
            PropertyList({
                Property("led_count", kPropertyTypeInteger, 1, PORT_B_WS2812_MAX_LEDS)
            }),
            [this](const PropertyList& props) -> ReturnValue {
                cJSON* root = cJSON_CreateObject();
                uint16_t led_count = static_cast<uint16_t>(props["led_count"].value<int>());
                esp_err_t err = InitPortBWs2812(led_count);
                bool ok = (err == ESP_OK);
                cJSON_AddBoolToObject(root, "available", ok);
                cJSON_AddBoolToObject(root, "ok", ok);
                cJSON_AddNumberToObject(root, "led_count", led_count);
                if (!ok) {
                    cJSON_AddStringToObject(root, "error", esp_err_to_name(err));
                }
                ESP_LOGI(TAG, "port_b.ws2812.init led_count=%u ok=%d",
                         (unsigned)led_count, ok ? 1 : 0);
                return root;
            });

        mcp_server.AddTool(
            "self.port_b.ws2812.set_pixel",
            "Set one LED in the Port B WS2812 strip buffer. Call "
            "self.port_b.ws2812.init first; until init succeeds this returns "
            "{\"available\":false,\"ok\":false}. index is 0..255, but the "
            "effective range is 0..(led_count-1); out-of-range requests "
            "return ok=false with error=\"index out of range\". r, g, and b "
            "are 0..255. By default the color is buffered only; pass "
            "refresh=true to immediately latch it to the strip, or call "
            "self.port_b.ws2812.refresh after several buffered updates. "
            "Runtime led_strip failures return ok=false with error. Port B "
            "outputs 3.3 V CMOS data on GPIO 9; older strict 5 V WS2812 "
            "variants may require a level shifter.",
            PropertyList({
                Property("index", kPropertyTypeInteger, 0, PORT_B_WS2812_MAX_LEDS - 1),
                Property("r", kPropertyTypeInteger, 0, 255),
                Property("g", kPropertyTypeInteger, 0, 255),
                Property("b", kPropertyTypeInteger, 0, 255),
                Property("refresh", kPropertyTypeBoolean, false)
            }),
            [this](const PropertyList& props) -> ReturnValue {
                cJSON* root = cJSON_CreateObject();
                cJSON_AddBoolToObject(root, "available", ws2812_ok_);
                if (!ws2812_ok_ || ws2812_handle_ == nullptr) {
                    cJSON_AddBoolToObject(root, "ok", false);
                    cJSON_AddStringToObject(root, "error",
                                            "Port B WS2812 strip not initialized.");
                    return root;
                }
                int index = props["index"].value<int>();
                if (index >= ws2812_led_count_) {
                    cJSON_AddBoolToObject(root, "ok", false);
                    cJSON_AddStringToObject(root, "error", "index out of range");
                    ESP_LOGW(TAG, "port_b.ws2812.set_pixel index=%d out of range (led_count=%u)",
                             index, (unsigned)ws2812_led_count_);
                    return root;
                }
                uint8_t r = ClampByte(props["r"].value<int>());
                uint8_t g = ClampByte(props["g"].value<int>());
                uint8_t b = ClampByte(props["b"].value<int>());
                bool refresh = props["refresh"].value<bool>();
                esp_err_t err = led_strip_set_pixel(ws2812_handle_, index, r, g, b);
                if (err == ESP_OK && refresh) {
                    err = led_strip_refresh(ws2812_handle_);
                }
                bool ok = (err == ESP_OK);
                cJSON_AddBoolToObject(root, "ok", ok);
                if (!ok) {
                    cJSON_AddStringToObject(root, "error", esp_err_to_name(err));
                }
                ESP_LOGI(TAG, "port_b.ws2812.set_pixel index=%d rgb=(%u,%u,%u) refresh=%d ok=%d",
                         index, r, g, b, refresh ? 1 : 0, ok ? 1 : 0);
                return root;
            });

        mcp_server.AddTool(
            "self.port_b.ws2812.set_strip",
            "Set multiple LEDs in the Port B WS2812 strip and refresh "
            "immediately. Call self.port_b.ws2812.init first; until init "
            "succeeds this returns {\"available\":false,\"ok\":false}. "
            "colors is a JSON-encoded array of [r,g,b] integer triples, "
            "for example \"[[255,0,0],[0,255,0],[0,0,255]]\". Entries are "
            "applied from LED index 0; up to led_count entries are written, "
            "extras are ignored, and missing trailing entries preserve the "
            "previous buffered values. The payload is validate-then-write: "
            "a malformed entry leaves the strip buffer unchanged. This tool "
            "auto-refreshes and is the preferred path for animation frames. "
            "Runtime led_strip failures return ok=false with error. Port B "
            "outputs 3.3 V CMOS data on GPIO 9; older strict 5 V WS2812 "
            "variants may require a level shifter.",
            PropertyList({Property("colors", kPropertyTypeString)}),
            [this](const PropertyList& props) -> ReturnValue {
                cJSON* root = cJSON_CreateObject();
                cJSON_AddBoolToObject(root, "available", ws2812_ok_);
                if (!ws2812_ok_ || ws2812_handle_ == nullptr) {
                    cJSON_AddBoolToObject(root, "ok", false);
                    cJSON_AddNumberToObject(root, "written", 0);
                    cJSON_AddStringToObject(root, "error",
                                            "Port B WS2812 strip not initialized.");
                    return root;
                }

                std::string json = props["colors"].value<std::string>();
                cJSON* arr = cJSON_Parse(json.c_str());
                if (arr == nullptr || !cJSON_IsArray(arr)) {
                    cJSON_AddBoolToObject(root, "ok", false);
                    cJSON_AddNumberToObject(root, "written", 0);
                    cJSON_AddStringToObject(root, "error",
                                            "colors must be a JSON array of [r,g,b] triples");
                    if (arr != nullptr) cJSON_Delete(arr);
                    return root;
                }

                int n = cJSON_GetArraySize(arr);
                if (n > ws2812_led_count_) n = ws2812_led_count_;
                std::vector<uint8_t> rgb;
                rgb.reserve(static_cast<size_t>(n) * 3);
                bool parse_ok = true;
                for (int i = 0; i < n; i++) {
                    cJSON* triple = cJSON_GetArrayItem(arr, i);
                    if (!cJSON_IsArray(triple) || cJSON_GetArraySize(triple) != 3) {
                        parse_ok = false;
                        break;
                    }
                    uint8_t r = 0, g = 0, b = 0;
                    if (!JsonByte(cJSON_GetArrayItem(triple, 0), &r) ||
                        !JsonByte(cJSON_GetArrayItem(triple, 1), &g) ||
                        !JsonByte(cJSON_GetArrayItem(triple, 2), &b)) {
                        parse_ok = false;
                        break;
                    }
                    rgb.push_back(r);
                    rgb.push_back(g);
                    rgb.push_back(b);
                }
                cJSON_Delete(arr);

                if (!parse_ok) {
                    cJSON_AddBoolToObject(root, "ok", false);
                    cJSON_AddNumberToObject(root, "written", 0);
                    cJSON_AddStringToObject(root, "error",
                                            "Each entry must be a [r,g,b] triple of integers 0..255");
                    ESP_LOGW(TAG, "port_b.ws2812.set_strip rejected malformed colors payload");
                    return root;
                }

                esp_err_t err = ESP_OK;
                for (int i = 0; i < n; i++) {
                    size_t offset = static_cast<size_t>(i) * 3;
                    err = led_strip_set_pixel(ws2812_handle_, i,
                                              rgb[offset + 0],
                                              rgb[offset + 1],
                                              rgb[offset + 2]);
                    if (err != ESP_OK) {
                        break;
                    }
                }
                if (err == ESP_OK) {
                    err = led_strip_refresh(ws2812_handle_);
                }

                bool ok = (err == ESP_OK);
                cJSON_AddBoolToObject(root, "ok", ok);
                cJSON_AddNumberToObject(root, "written", ok ? n : 0);
                if (!ok) {
                    cJSON_AddStringToObject(root, "error", esp_err_to_name(err));
                }
                ESP_LOGI(TAG, "port_b.ws2812.set_strip written=%d ok=%d",
                         ok ? n : 0, ok ? 1 : 0);
                return root;
            });

        mcp_server.AddTool(
            "self.port_b.ws2812.refresh",
            "Refresh the Port B WS2812 strip, latching the current buffered "
            "colors out on CoreS3 HY2.0-4P digital OUTPUT GPIO 9. Call "
            "self.port_b.ws2812.init first; until init succeeds this returns "
            "{\"available\":false,\"ok\":false}. Use this after one or more "
            "self.port_b.ws2812.set_pixel calls made with refresh=false. "
            "Runtime led_strip failures return ok=false with error. Port B "
            "outputs 3.3 V CMOS data; older strict 5 V WS2812 variants may "
            "require a level shifter.",
            PropertyList(),
            [this](const PropertyList&) -> ReturnValue {
                cJSON* root = cJSON_CreateObject();
                cJSON_AddBoolToObject(root, "available", ws2812_ok_);
                if (!ws2812_ok_ || ws2812_handle_ == nullptr) {
                    cJSON_AddBoolToObject(root, "ok", false);
                    cJSON_AddStringToObject(root, "error",
                                            "Port B WS2812 strip not initialized.");
                    return root;
                }
                esp_err_t err = led_strip_refresh(ws2812_handle_);
                bool ok = (err == ESP_OK);
                cJSON_AddBoolToObject(root, "ok", ok);
                if (!ok) {
                    cJSON_AddStringToObject(root, "error", esp_err_to_name(err));
                }
                ESP_LOGI(TAG, "port_b.ws2812.refresh ok=%d", ok ? 1 : 0);
                return root;
            });

        mcp_server.AddTool(
            "self.port_b.ws2812.clear",
            "Turn off every LED in the Port B WS2812 strip and refresh "
            "immediately on CoreS3 HY2.0-4P digital OUTPUT GPIO 9. Call "
            "self.port_b.ws2812.init first; until init succeeds this returns "
            "{\"available\":false,\"ok\":false}. This is equivalent to "
            "self.port_b.ws2812.set_strip with an all-zero array of length "
            "led_count, and it clears the driver's sticky per-pixel buffer. "
            "Runtime led_strip failures return ok=false with error. Port B "
            "outputs 3.3 V CMOS data; older strict 5 V WS2812 variants may "
            "require a level shifter.",
            PropertyList(),
            [this](const PropertyList&) -> ReturnValue {
                cJSON* root = cJSON_CreateObject();
                cJSON_AddBoolToObject(root, "available", ws2812_ok_);
                if (!ws2812_ok_ || ws2812_handle_ == nullptr) {
                    cJSON_AddBoolToObject(root, "ok", false);
                    cJSON_AddStringToObject(root, "error",
                                            "Port B WS2812 strip not initialized.");
                    return root;
                }
                esp_err_t err = led_strip_clear(ws2812_handle_);
                bool ok = (err == ESP_OK);
                cJSON_AddBoolToObject(root, "ok", ok);
                if (!ok) {
                    cJSON_AddStringToObject(root, "error", esp_err_to_name(err));
                }
                ESP_LOGI(TAG, "port_b.ws2812.clear ok=%d", ok ? 1 : 0);
                return root;
            });

        // ---- Generic Port C WS2812 strip tools ----
        // Expose the CoreS3 Port C signal 1 (GPIO 17) as a generic
        // WS2812-compatible strip driver. This is independent from self.led.*,
        // which drives the 12-LED base strip through the PY32 I2C path.

        mcp_server.AddTool(
            "self.port_c.ws2812.init",
            "Initialize a WS2812-compatible LED strip connected to Port C "
            "(CoreS3 HY2.0-4P signal 1, GPIO 17). led_count is the "
            "number of LEDs in the strip (1..256). This allocates the "
            "ESP-IDF led_strip RMT backend and must succeed before calling "
            "self.port_c.ws2812.set_pixel, set_strip, refresh, or clear. "
            "Repeated calls with the same led_count are no-ops; a different "
            "led_count tears down and rebuilds the strip handle. Returns "
            "{\"available\":true,\"ok\":true,\"led_count\":N} on success or "
            "{\"available\":false,\"ok\":false,\"led_count\":N,"
            "\"error\":\"ESP_ERR_...\"} on failure. The strip protocol is "
            "3.3 V CMOS data on GPIO 17; most modern WS2812B-V5/B2 strips "
            "tolerate this, while older strict 5 V V_IH variants may need "
            "an external level shifter.",
            PropertyList({
                Property("led_count", kPropertyTypeInteger, 1, PORT_C_WS2812_MAX_LEDS)
            }),
            [this](const PropertyList& props) -> ReturnValue {
                cJSON* root = cJSON_CreateObject();
                uint16_t led_count = static_cast<uint16_t>(props["led_count"].value<int>());
                esp_err_t err = InitPortCWs2812(led_count);
                bool ok = (err == ESP_OK);
                cJSON_AddBoolToObject(root, "available", ok);
                cJSON_AddBoolToObject(root, "ok", ok);
                cJSON_AddNumberToObject(root, "led_count", led_count);
                if (!ok) {
                    cJSON_AddStringToObject(root, "error", esp_err_to_name(err));
                }
                ESP_LOGI(TAG, "port_c.ws2812.init led_count=%u ok=%d",
                         (unsigned)led_count, ok ? 1 : 0);
                return root;
            });

        mcp_server.AddTool(
            "self.port_c.ws2812.set_pixel",
            "Set one LED in the Port C WS2812 strip buffer. Call "
            "self.port_c.ws2812.init first; until init succeeds this returns "
            "{\"available\":false,\"ok\":false}. index is 0..255, but the "
            "effective range is 0..(led_count-1); out-of-range requests "
            "return ok=false with error=\"index out of range\". r, g, and b "
            "are 0..255. By default the color is buffered only; pass "
            "refresh=true to immediately latch it to the strip, or call "
            "self.port_c.ws2812.refresh after several buffered updates. "
            "Runtime led_strip failures return ok=false with error. Port C "
            "outputs 3.3 V CMOS data on GPIO 17; older strict 5 V WS2812 "
            "variants may require a level shifter.",
            PropertyList({
                Property("index", kPropertyTypeInteger, 0, PORT_C_WS2812_MAX_LEDS - 1),
                Property("r", kPropertyTypeInteger, 0, 255),
                Property("g", kPropertyTypeInteger, 0, 255),
                Property("b", kPropertyTypeInteger, 0, 255),
                Property("refresh", kPropertyTypeBoolean, false)
            }),
            [this](const PropertyList& props) -> ReturnValue {
                cJSON* root = cJSON_CreateObject();
                cJSON_AddBoolToObject(root, "available", port_c_ws2812_ok_);
                if (!port_c_ws2812_ok_ || port_c_ws2812_handle_ == nullptr) {
                    cJSON_AddBoolToObject(root, "ok", false);
                    cJSON_AddStringToObject(root, "error",
                                            "Port C WS2812 strip not initialized.");
                    return root;
                }
                int index = props["index"].value<int>();
                if (index >= port_c_ws2812_led_count_) {
                    cJSON_AddBoolToObject(root, "ok", false);
                    cJSON_AddStringToObject(root, "error", "index out of range");
                    ESP_LOGW(TAG, "port_c.ws2812.set_pixel index=%d out of range (led_count=%u)",
                             index, (unsigned)port_c_ws2812_led_count_);
                    return root;
                }
                uint8_t r = ClampByte(props["r"].value<int>());
                uint8_t g = ClampByte(props["g"].value<int>());
                uint8_t b = ClampByte(props["b"].value<int>());
                bool refresh = props["refresh"].value<bool>();
                esp_err_t err = led_strip_set_pixel(port_c_ws2812_handle_, index, r, g, b);
                if (err == ESP_OK && refresh) {
                    err = led_strip_refresh(port_c_ws2812_handle_);
                }
                bool ok = (err == ESP_OK);
                cJSON_AddBoolToObject(root, "ok", ok);
                if (!ok) {
                    cJSON_AddStringToObject(root, "error", esp_err_to_name(err));
                }
                ESP_LOGI(TAG, "port_c.ws2812.set_pixel index=%d rgb=(%u,%u,%u) refresh=%d ok=%d",
                         index, r, g, b, refresh ? 1 : 0, ok ? 1 : 0);
                return root;
            });

        mcp_server.AddTool(
            "self.port_c.ws2812.set_strip",
            "Set multiple LEDs in the Port C WS2812 strip and refresh "
            "immediately. Call self.port_c.ws2812.init first; until init "
            "succeeds this returns {\"available\":false,\"ok\":false}. "
            "colors is a JSON-encoded array of [r,g,b] integer triples, "
            "for example \"[[255,0,0],[0,255,0],[0,0,255]]\". Entries are "
            "applied from LED index 0; up to led_count entries are written, "
            "extras are ignored, and missing trailing entries preserve the "
            "previous buffered values. The payload is validate-then-write: "
            "a malformed entry leaves the strip buffer unchanged. This tool "
            "auto-refreshes and is the preferred path for animation frames. "
            "Runtime led_strip failures return ok=false with error. Port C "
            "outputs 3.3 V CMOS data on GPIO 17; older strict 5 V WS2812 "
            "variants may require a level shifter.",
            PropertyList({Property("colors", kPropertyTypeString)}),
            [this](const PropertyList& props) -> ReturnValue {
                cJSON* root = cJSON_CreateObject();
                cJSON_AddBoolToObject(root, "available", port_c_ws2812_ok_);
                if (!port_c_ws2812_ok_ || port_c_ws2812_handle_ == nullptr) {
                    cJSON_AddBoolToObject(root, "ok", false);
                    cJSON_AddNumberToObject(root, "written", 0);
                    cJSON_AddStringToObject(root, "error",
                                            "Port C WS2812 strip not initialized.");
                    return root;
                }

                std::string json = props["colors"].value<std::string>();
                cJSON* arr = cJSON_Parse(json.c_str());
                if (arr == nullptr || !cJSON_IsArray(arr)) {
                    cJSON_AddBoolToObject(root, "ok", false);
                    cJSON_AddNumberToObject(root, "written", 0);
                    cJSON_AddStringToObject(root, "error",
                                            "colors must be a JSON array of [r,g,b] triples");
                    if (arr != nullptr) cJSON_Delete(arr);
                    return root;
                }

                int n = cJSON_GetArraySize(arr);
                if (n > port_c_ws2812_led_count_) n = port_c_ws2812_led_count_;
                std::vector<uint8_t> rgb;
                rgb.reserve(static_cast<size_t>(n) * 3);
                bool parse_ok = true;
                for (int i = 0; i < n; i++) {
                    cJSON* triple = cJSON_GetArrayItem(arr, i);
                    if (!cJSON_IsArray(triple) || cJSON_GetArraySize(triple) != 3) {
                        parse_ok = false;
                        break;
                    }
                    uint8_t r = 0, g = 0, b = 0;
                    if (!JsonByte(cJSON_GetArrayItem(triple, 0), &r) ||
                        !JsonByte(cJSON_GetArrayItem(triple, 1), &g) ||
                        !JsonByte(cJSON_GetArrayItem(triple, 2), &b)) {
                        parse_ok = false;
                        break;
                    }
                    rgb.push_back(r);
                    rgb.push_back(g);
                    rgb.push_back(b);
                }
                cJSON_Delete(arr);

                if (!parse_ok) {
                    cJSON_AddBoolToObject(root, "ok", false);
                    cJSON_AddNumberToObject(root, "written", 0);
                    cJSON_AddStringToObject(root, "error",
                                            "Each entry must be a [r,g,b] triple of integers 0..255");
                    ESP_LOGW(TAG, "port_c.ws2812.set_strip rejected malformed colors payload");
                    return root;
                }

                esp_err_t err = ESP_OK;
                for (int i = 0; i < n; i++) {
                    size_t offset = static_cast<size_t>(i) * 3;
                    err = led_strip_set_pixel(port_c_ws2812_handle_, i,
                                              rgb[offset + 0],
                                              rgb[offset + 1],
                                              rgb[offset + 2]);
                    if (err != ESP_OK) {
                        break;
                    }
                }
                if (err == ESP_OK) {
                    err = led_strip_refresh(port_c_ws2812_handle_);
                }

                bool ok = (err == ESP_OK);
                cJSON_AddBoolToObject(root, "ok", ok);
                cJSON_AddNumberToObject(root, "written", ok ? n : 0);
                if (!ok) {
                    cJSON_AddStringToObject(root, "error", esp_err_to_name(err));
                }
                ESP_LOGI(TAG, "port_c.ws2812.set_strip written=%d ok=%d",
                         ok ? n : 0, ok ? 1 : 0);
                return root;
            });

        mcp_server.AddTool(
            "self.port_c.ws2812.refresh",
            "Refresh the Port C WS2812 strip, latching the current buffered "
            "colors out on CoreS3 HY2.0-4P signal 1 GPIO 17. Call "
            "self.port_c.ws2812.init first; until init succeeds this returns "
            "{\"available\":false,\"ok\":false}. Use this after one or more "
            "self.port_c.ws2812.set_pixel calls made with refresh=false. "
            "Runtime led_strip failures return ok=false with error. Port C "
            "outputs 3.3 V CMOS data; older strict 5 V WS2812 variants may "
            "require a level shifter.",
            PropertyList(),
            [this](const PropertyList&) -> ReturnValue {
                cJSON* root = cJSON_CreateObject();
                cJSON_AddBoolToObject(root, "available", port_c_ws2812_ok_);
                if (!port_c_ws2812_ok_ || port_c_ws2812_handle_ == nullptr) {
                    cJSON_AddBoolToObject(root, "ok", false);
                    cJSON_AddStringToObject(root, "error",
                                            "Port C WS2812 strip not initialized.");
                    return root;
                }
                esp_err_t err = led_strip_refresh(port_c_ws2812_handle_);
                bool ok = (err == ESP_OK);
                cJSON_AddBoolToObject(root, "ok", ok);
                if (!ok) {
                    cJSON_AddStringToObject(root, "error", esp_err_to_name(err));
                }
                ESP_LOGI(TAG, "port_c.ws2812.refresh ok=%d", ok ? 1 : 0);
                return root;
            });

        mcp_server.AddTool(
            "self.port_c.ws2812.clear",
            "Turn off every LED in the Port C WS2812 strip and refresh "
            "immediately on CoreS3 HY2.0-4P signal 1 GPIO 17. Call "
            "self.port_c.ws2812.init first; until init succeeds this returns "
            "{\"available\":false,\"ok\":false}. This is equivalent to "
            "self.port_c.ws2812.set_strip with an all-zero array of length "
            "led_count, and it clears the driver's sticky per-pixel buffer. "
            "Runtime led_strip failures return ok=false with error. Port C "
            "outputs 3.3 V CMOS data; older strict 5 V WS2812 variants may "
            "require a level shifter.",
            PropertyList(),
            [this](const PropertyList&) -> ReturnValue {
                cJSON* root = cJSON_CreateObject();
                cJSON_AddBoolToObject(root, "available", port_c_ws2812_ok_);
                if (!port_c_ws2812_ok_ || port_c_ws2812_handle_ == nullptr) {
                    cJSON_AddBoolToObject(root, "ok", false);
                    cJSON_AddStringToObject(root, "error",
                                            "Port C WS2812 strip not initialized.");
                    return root;
                }
                esp_err_t err = led_strip_clear(port_c_ws2812_handle_);
                bool ok = (err == ESP_OK);
                cJSON_AddBoolToObject(root, "ok", ok);
                if (!ok) {
                    cJSON_AddStringToObject(root, "error", esp_err_to_name(err));
                }
                ESP_LOGI(TAG, "port_c.ws2812.clear ok=%d", ok ? 1 : 0);
                return root;
            });

        ESP_LOGI(TAG, "StackChan MCP tools registered");
    }

public:
    StackChanBoard() {
        InitializePowerSaveTimer();
        InitializeI2c();
        InitializePortAI2c();
        InitializeAxp2101();
        InitializeAw9523();
        // I2cDetect() moved AFTER all I2C device initializations.
        // The 128-address probe (i2c_master_probe over the whole bus) was
        // leaving PY32 (0x6F) in a half-finished slave state, so the
        // following transmit_receive (REG_VERSION via Repeated Start)
        // timed out (0x103). Doing the scan after IOExpander/Si12T init
        // preserves the boot-log debug info without poisoning subsequent
        // register reads. Si12T (0x68) is unaffected on the same bus,
        // but moving the scan is safer for any future I2C peripheral too.
        InitializeSpi();
        InitializeIli9342Display();
        screensaver_last_activity_us_.store(
            esp_timer_get_time(), std::memory_order_release);
        InitializeCamera();
        InitializeFt6336TouchPad();
        GetBacklight()->RestoreBrightness();
        InitializeIOExpander();
        InitializeServo();
        InitializeTouchSettings();
        InitializeSi12tTouch();
        I2cDetect();
        RegisterMcpTools();
        StartStackChanUsbControl(this);
    }

    StackChanExpressionOutcome StartExpressionPreview(
            const std::string& name,
            const StackChanExpressionRecipe& recipe) override {
        return StartExpression(
            name, recipe, ExpressionInvocation::PREVIEW);
    }

    bool AbortExpressionPreview() override {
        if (!expression_active_.load(std::memory_order_acquire) ||
            expression_invocation_.load(std::memory_order_acquire) !=
                ExpressionInvocation::PREVIEW) {
            return false;
        }
        expression_abort_requested_.store(true, std::memory_order_release);
        return true;
    }

    bool TakeExpressionPreviewResult(
            StackChanExpressionOutcome& outcome) override {
        if (!expression_preview_result_ready_.exchange(
                false, std::memory_order_acq_rel)) {
            return false;
        }
        outcome = expression_preview_result_.load(std::memory_order_relaxed);
        return true;
    }

    bool BeginFirmwareMaintenance() override {
        PhysicalBehaviorOwner expected = PhysicalBehaviorOwner::IDLE;
        if (physical_behavior_owner_.compare_exchange_strong(
                expected,
                PhysicalBehaviorOwner::MAINTENANCE_RESERVED,
                std::memory_order_acq_rel)) {
            if (!servo_ok_ || motion_driver_ == nullptr ||
                PhysicalMotionInactive()) {
                HideFace();
                UpdateDisplayMode(kDeviceStateUpgrading, true);
                return true;
            }
            physical_behavior_owner_.store(
                PhysicalBehaviorOwner::IDLE, std::memory_order_release);
            return false;
        }
        return false;
    }

    bool ConsumeFirmwareMaintenance() override {
        PhysicalBehaviorOwner expected =
            PhysicalBehaviorOwner::MAINTENANCE_RESERVED;
        return physical_behavior_owner_.compare_exchange_strong(
            expected,
            PhysicalBehaviorOwner::MAINTENANCE,
            std::memory_order_acq_rel);
    }

    void EndFirmwareMaintenance() override {
        PhysicalBehaviorOwner expected = PhysicalBehaviorOwner::MAINTENANCE;
        if (!physical_behavior_owner_.compare_exchange_strong(
                expected,
                PhysicalBehaviorOwner::IDLE,
                std::memory_order_acq_rel)) {
            expected = PhysicalBehaviorOwner::MAINTENANCE_RESERVED;
            physical_behavior_owner_.compare_exchange_strong(
                expected,
                PhysicalBehaviorOwner::IDLE,
                std::memory_order_acq_rel);
        }
        if (Application::GetInstance().GetDeviceState() ==
                kDeviceStateIdle) {
            ShowIdleFace();
        }
    }

    void SetNetworkEventCallback(NetworkEventCallback callback) override {
        WifiBoard::SetNetworkEventCallback(
            [this, callback = std::move(callback)](
                NetworkEvent event, const std::string& data) {
                if (callback) callback(event, data);
                if (event == NetworkEvent::Connected) {
                    RefreshPublicIpLocationForWifi(data);
                }
            });
    }

    void OnAssetsUpdated() override {
        if (display_ != nullptr) {
            DisplayLockGuard lock(display_);
            ResetScreenSaverResourcesLocked();
        }
        if (physical_behavior_owner_.load(std::memory_order_acquire) ==
                PhysicalBehaviorOwner::IDLE &&
            Application::GetInstance().GetDeviceState() ==
                kDeviceStateIdle) {
            ShowIdleFace();
        }
    }

    void OnDeviceStateChanged(DeviceState state) override {
        const bool deferred_speech = state == kDeviceStateSpeaking &&
            expression_invocation_.load(std::memory_order_acquire) !=
                ExpressionInvocation::PREVIEW;
        if (state != kDeviceStateIdle && !deferred_speech &&
            expression_active_.load(std::memory_order_acquire)) {
            expression_abort_requested_.store(
                true, std::memory_order_release);
        }
        if (state == kDeviceStateIdle) {
            PrepareScreenSaver();
            if (!expression_active_.load(std::memory_order_acquire)) {
                ShowIdleFace();
            }
        } else if (state == kDeviceStateListening) {
            if (!expression_active_.load(std::memory_order_acquire)) {
                ShowListeningFace();
            }
        } else if (state != kDeviceStateListening &&
                   state != kDeviceStateSpeaking) {
            HideFace();
        }
        UpdateDisplayMode(state, true);
    }

    virtual AudioCodec* GetAudioCodec() override {
        static CoreS3AudioCodec audio_codec(i2c_bus_,
            AUDIO_INPUT_SAMPLE_RATE,
            AUDIO_OUTPUT_SAMPLE_RATE,
            AUDIO_I2S_GPIO_MCLK,
            AUDIO_I2S_GPIO_BCLK,
            AUDIO_I2S_GPIO_WS,
            AUDIO_I2S_GPIO_DOUT,
            AUDIO_I2S_GPIO_DIN,
            AUDIO_CODEC_AW88298_ADDR,
            AUDIO_CODEC_ES7210_ADDR,
            AUDIO_INPUT_REFERENCE);
        return &audio_codec;
    }

    virtual Display* GetDisplay() override {
        return display_;
    }

    virtual Camera* GetCamera() override {
        return camera_;
    }

    virtual bool GetBatteryLevel(int &level, bool& charging, bool& discharging) override {
        discharging = pmic_->IsDischarging();
        charging = !discharging &&
            (pmic_->IsCharging() || pmic_->IsChargingDone());
        // SetEnabled is idempotent. Applying the observed source state on
        // every telemetry update also handles an externally powered boot,
        // where the initial false must disable the timer enabled at setup.
        power_save_timer_->SetEnabled(discharging);

        level = pmic_->GetBatteryLevel();
        return true;
    }

    virtual void SetPowerSaveLevel(PowerSaveLevel level) override {
        if (level != PowerSaveLevel::LOW_POWER) {
            power_save_timer_->WakeUp();
        }
        WifiBoard::SetPowerSaveLevel(level);
    }

    virtual bool CanPowerSaveWithTransport() override {
        return true;
    }

    virtual void OnTtsStart() override {
        ShowFaceAsset("speaking.gif", 0, true);
    }

    virtual void OnTtsStop() override {
        if (FaceDisplayAllowed()) {
            RestoreFaceForCurrentState();
        } else {
            HideFace();
        }
    }

    bool ShouldDeferAudioPlayback() const override {
        const auto owner =
            physical_behavior_owner_.load(std::memory_order_acquire);
        return owner == PhysicalBehaviorOwner::EXPRESSION;
    }


    // --------------------------------------------------------------------

    virtual Backlight *GetBacklight() override {
        static CustomBacklight backlight(pmic_);
        return &backlight;
    }
};

DECLARE_BOARD(StackChanBoard);
