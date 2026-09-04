#ifndef STACKCHAN_USB_CONTROL_H_
#define STACKCHAN_USB_CONTROL_H_

#include <array>
#include <cstddef>
#include <string>

constexpr size_t kStackChanExpressionMaxSteps = 5;
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
    int yaw = 0;
    int pitch = 43;
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
    int schema_version = 1;
    size_t step_count = 0;
    std::array<StackChanExpressionStep, kStackChanExpressionMaxSteps> steps;
};

class StackChanExpressionPreviewer {
public:
    virtual ~StackChanExpressionPreviewer() = default;
    virtual bool PreviewExpression(
        const std::string& name,
        const StackChanExpressionRecipe& recipe,
        std::string& error) = 0;
};

void StartStackChanUsbControl(StackChanExpressionPreviewer* expressions);

#endif  // STACKCHAN_USB_CONTROL_H_
