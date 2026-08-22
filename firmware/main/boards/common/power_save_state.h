#pragma once

#include <cstdint>

enum class PowerSaveTransition : uint8_t {
    NONE = 0,
    ENTER_DIM,
    ENTER_SLEEP,
    EXIT_DIM,
    EXIT_SLEEP,
};

class PowerSaveState {
public:
    explicit PowerSaveState(int seconds_to_sleep)
        : seconds_to_sleep_(seconds_to_sleep) {}

    PowerSaveTransition SetEnabled(bool enabled) {
        if (enabled == enabled_) {
            return PowerSaveTransition::NONE;
        }
        enabled_ = enabled;
        if (!enabled) {
            return WakeUp();
        }
        ticks_ = 0;
        return PowerSaveTransition::NONE;
    }

    PowerSaveTransition Tick(bool can_sleep) {
        if (!enabled_) {
            return PowerSaveTransition::NONE;
        }
        if (!sleeping_ && !can_sleep) {
            return WakeUp();
        }

        ticks_++;
        if (
            seconds_to_sleep_ > 1 &&
            !dimmed_ &&
            ticks_ >= seconds_to_sleep_ / 2
        ) {
            dimmed_ = true;
            return PowerSaveTransition::ENTER_DIM;
        }
        if (
            seconds_to_sleep_ != -1 &&
            !sleeping_ &&
            ticks_ >= seconds_to_sleep_
        ) {
            sleeping_ = true;
            return PowerSaveTransition::ENTER_SLEEP;
        }
        return PowerSaveTransition::NONE;
    }

    PowerSaveTransition WakeUp() {
        ticks_ = 0;
        if (sleeping_) {
            sleeping_ = false;
            dimmed_ = false;
            return PowerSaveTransition::EXIT_SLEEP;
        }
        if (dimmed_) {
            dimmed_ = false;
            return PowerSaveTransition::EXIT_DIM;
        }
        return PowerSaveTransition::NONE;
    }

    bool enabled() const { return enabled_; }
    bool dimmed() const { return dimmed_; }
    bool sleeping() const { return sleeping_; }
    int ticks() const { return ticks_; }

private:
    bool enabled_ = false;
    bool dimmed_ = false;
    bool sleeping_ = false;
    int ticks_ = 0;
    int seconds_to_sleep_;
};
