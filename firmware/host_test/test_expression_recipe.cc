#include <gtest/gtest.h>

#include "expression_recipe.h"

namespace {

StackChanExpressionRecipe Recipe(int duration_ms = 400, int via = 8) {
    StackChanExpressionRecipe recipe;
    recipe.step_count = 1;
    auto& curve = recipe.steps[0];
    curve.type = StackChanExpressionStepType::CURVE;
    curve.duration_ms = duration_ms;
    curve.points = {{{0, 43}, {via, 48}, {via, 48}, {0, 43}}};
    return recipe;
}

TEST(ExpressionRecipe, ValidatesMotionBounds) {
    std::string error;
    EXPECT_TRUE(ValidateStackChanExpressionRecipe(Recipe(), error)) << error;
    EXPECT_FALSE(ValidateStackChanExpressionRecipe(Recipe(100, 90), error));
    EXPECT_NE(error.find("velocity"), std::string::npos);

    auto stationary = Recipe();
    stationary.steps[0].points = {
        {{0, 43}, {0, 43}, {0, 43}, {0, 43}}
    };
    EXPECT_FALSE(ValidateStackChanExpressionRecipe(stationary, error));
    EXPECT_NE(error.find("move"), std::string::npos);
}

TEST(ExpressionRecipe, UsesSharedMathAtBoundaries) {
    const auto recipe = Recipe();
    const auto& curve = recipe.steps[0];
    EXPECT_EQ(EvaluateStackChanExpressionAxis(curve, true, 0), 0);
    EXPECT_EQ(EvaluateStackChanExpressionAxis(curve, false, 0), 43);
    EXPECT_EQ(EvaluateStackChanExpressionAxis(curve, true, 400), 0);
    EXPECT_EQ(EvaluateStackChanExpressionAxis(curve, false, 401), 43);
    EXPECT_TRUE(IsStackChanExpressionVelocitySafe(0, 7, 30));
    EXPECT_FALSE(IsStackChanExpressionVelocitySafe(0, 8, 30));
    EXPECT_FALSE(IsStackChanExpressionVelocitySafe(
        -2147483647, 2147483647, 30));

    const std::array<int, 4> native_points = {{460, 492, 492, 460}};
    EXPECT_EQ(EvaluateStackChanExpressionCurve(
        native_points, 400, 0), 460);
    EXPECT_EQ(EvaluateStackChanExpressionCurve(
        native_points, 400, 200), 484);
    EXPECT_EQ(EvaluateStackChanExpressionCurve(
        native_points, 400, 400), 460);
    EXPECT_TRUE(IsStackChanExpressionVelocitySafe(
        0, 4800, 20, 240, 1000));
    EXPECT_FALSE(IsStackChanExpressionVelocitySafe(
        0, 4801, 20, 240, 1000));
}

TEST(ExpressionRecipe, AdvancesTheReviewedSampleSchedule) {
    EXPECT_EQ(NextStackChanExpressionSampleElapsedMs(0, 420), 20U);
    EXPECT_EQ(NextStackChanExpressionSampleElapsedMs(380, 420), 400U);
    EXPECT_EQ(NextStackChanExpressionSampleElapsedMs(400, 415), 415U);
    EXPECT_EQ(NextStackChanExpressionSampleElapsedMs(415, 415), 415U);
}

}  // namespace
