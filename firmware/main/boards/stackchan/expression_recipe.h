#ifndef STACKCHAN_EXPRESSION_RECIPE_H_
#define STACKCHAN_EXPRESSION_RECIPE_H_

#include <array>
#include <cstddef>
#include <cstdint>
#include <string>

struct cJSON;

constexpr size_t kStackChanExpressionMaxSteps = 5;
constexpr int kStackChanExpressionIdleYaw = 0;
constexpr int kStackChanExpressionIdlePitch = 43;
constexpr int kStackChanExpressionMaxSpeedDps = 240;
constexpr uint32_t kStackChanExpressionSampleIntervalMs = 20;
constexpr uint32_t kStackChanExpressionMaxRecipeDurationMs = 15000;

constexpr std::array<const char*, 7> kStackChanExpressionNames = {{
    "agree",
    "pleased",
    "curious",
    "concerned",
    "surprised",
    "embarrassed",
    "mischievous",
}};

struct StackChanExpressionPoint {
    int yaw = kStackChanExpressionIdleYaw;
    int pitch = kStackChanExpressionIdlePitch;
};

enum class StackChanExpressionStepType {
    CURVE,
    PAUSE,
};

struct StackChanExpressionStep {
    StackChanExpressionStepType type = StackChanExpressionStepType::PAUSE;
    int duration_ms = 0;
    std::array<StackChanExpressionPoint, 4> points;
};

struct StackChanExpressionRecipe {
    int schema_version = 2;
    std::string animation;
    size_t step_count = 0;
    std::array<StackChanExpressionStep, kStackChanExpressionMaxSteps> steps;
};

enum class StackChanExpressionOutcome : uint8_t {
    STARTED = 0,
    COMPLETED,
    BUSY,
    INVALID_RECIPE,
    INTERRUPTED,
    MOTION_FAILED,
    SAFE_RETURN_FAILED,
    UNAVAILABLE,
    UNSUPPORTED_DRIVER,
};

enum class StackChanExpressionLoadStatus : uint8_t {
    OK = 0,
    NOT_CALIBRATED,
    INVALID,
};

bool IsStackChanExpressionName(const char* name);
bool IsStackChanExpressionName(const std::string& name);
bool IsStackChanExpressionRecipeName(const char* name);
bool IsStackChanExpressionRecipeName(const std::string& name);
const char* StackChanExpressionOutcomeName(StackChanExpressionOutcome outcome);

bool ParseStackChanExpressionRecipe(
    const cJSON* value,
    StackChanExpressionRecipe& recipe);
cJSON* EncodeStackChanExpressionRecipe(
    const StackChanExpressionRecipe& recipe);
bool ValidateStackChanExpressionRecipe(
    const StackChanExpressionRecipe& recipe,
    std::string& error);
uint32_t StackChanExpressionRecipeDurationMs(
    const StackChanExpressionRecipe& recipe);
uint32_t NextStackChanExpressionSampleElapsedMs(
    uint32_t elapsed_ms,
    uint32_t duration_ms);

int EvaluateStackChanExpressionAxis(
    const StackChanExpressionStep& step,
    bool yaw,
    uint32_t elapsed_ms);
int EvaluateStackChanExpressionCurve(
    const std::array<int, 4>& points,
    uint32_t duration_ms,
    uint32_t elapsed_ms);
bool IsStackChanExpressionVelocitySafe(
    int previous_target,
    int next_target,
    uint32_t command_window_ms,
    int maximum_speed_dps = kStackChanExpressionMaxSpeedDps,
    int units_per_degree = 1);

StackChanExpressionLoadStatus LoadStackChanExpressionRecipe(
    const std::string& name,
    StackChanExpressionRecipe& recipe);
bool SaveStackChanExpressionRecipe(
    const std::string& name,
    const StackChanExpressionRecipe& recipe,
    std::string& error);
void ResetStackChanExpressionRecipe(const std::string& name);

#endif  // STACKCHAN_EXPRESSION_RECIPE_H_
