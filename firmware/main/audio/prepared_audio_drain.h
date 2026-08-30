#ifndef PREPARED_AUDIO_DRAIN_H
#define PREPARED_AUDIO_DRAIN_H

#include <chrono>
#include <cstddef>
#include <cstdint>

enum class PreparedAudioDrainDecision {
    kWait,
    kComplete,
    kFail,
};

struct PreparedAudioDrainSnapshot {
    bool service_stopped = false;
    bool generation_current = false;
    bool output_failed = false;
    bool decode_queue_empty = false;
    bool playback_queue_empty = false;
    bool decode_in_flight = false;
    bool output_in_flight = false;
    size_t received_packets = 0;
    size_t decoded_packets = 0;
    size_t output_frames = 0;
};

inline bool IsCurrentPreparedAudioGeneration(
        uint32_t packet_generation,
        uint32_t current_generation,
        bool tracking) {
    return tracking && packet_generation != 0 &&
        packet_generation == current_generation;
}

inline PreparedAudioDrainDecision EvaluatePreparedAudioDrain(
        const PreparedAudioDrainSnapshot& snapshot) {
    if (snapshot.service_stopped || !snapshot.generation_current ||
        snapshot.output_failed) {
        return PreparedAudioDrainDecision::kFail;
    }

    bool accounting_complete = snapshot.received_packets != 0 &&
        snapshot.received_packets == snapshot.decoded_packets &&
        snapshot.decoded_packets == snapshot.output_frames;
    if (accounting_complete && snapshot.decode_queue_empty &&
        snapshot.playback_queue_empty && !snapshot.decode_in_flight &&
        !snapshot.output_in_flight) {
        return PreparedAudioDrainDecision::kComplete;
    }
    return PreparedAudioDrainDecision::kWait;
}

class PreparedAudioStallDeadline {
public:
    using Clock = std::chrono::steady_clock;

    PreparedAudioStallDeadline(
            size_t output_frames,
            Clock::time_point now,
            std::chrono::milliseconds timeout)
        : last_output_frames_(output_frames),
          deadline_(now + timeout),
          timeout_(timeout) {}

    void ObserveOutput(size_t output_frames, Clock::time_point now) {
        if (output_frames > last_output_frames_) {
            last_output_frames_ = output_frames;
            deadline_ = now + timeout_;
        }
    }

    Clock::time_point deadline() const {
        return deadline_;
    }

    bool Expired(Clock::time_point now) const {
        return now >= deadline_;
    }

private:
    size_t last_output_frames_;
    Clock::time_point deadline_;
    std::chrono::milliseconds timeout_;
};

#endif
