#ifndef STACKCHAN_USB_CONTROL_H_
#define STACKCHAN_USB_CONTROL_H_

#include <string>

#include "expression_recipe.h"

class StackChanExpressionController {
public:
    virtual ~StackChanExpressionController() = default;
    virtual StackChanExpressionOutcome StartExpressionPreview(
        const std::string& name,
        const StackChanExpressionRecipe& recipe) = 0;
    virtual bool AbortExpressionPreview() = 0;
    virtual bool TakeExpressionPreviewResult(
        StackChanExpressionOutcome& outcome) = 0;
};

void StartStackChanUsbControl(StackChanExpressionController* expressions);

#endif  // STACKCHAN_USB_CONTROL_H_
