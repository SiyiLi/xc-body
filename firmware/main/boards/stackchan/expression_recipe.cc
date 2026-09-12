#include "expression_recipe.h"

#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <cstring>

#ifndef STACKCHAN_EXPRESSION_HOST_TEST
#include <cJSON.h>

#include "settings.h"
#endif

namespace {

#ifndef STACKCHAN_EXPRESSION_HOST_TEST
constexpr char kExpressionSettingsNamespace[] = "expressions";
#endif
constexpr int kMinimumCurveDurationMs = 100;
constexpr int kMaximumStepDurationMs = 5000;
constexpr int kMinimumYaw = -90;
constexpr int kMaximumYaw = 90;
constexpr int kMinimumPitch = 5;
constexpr int kMaximumPitch = 85;
constexpr int kVelocityValidationUnitsPerDegree = 1000;

#ifndef STACKCHAN_EXPRESSION_HOST_TEST
bool IsInteger(const cJSON* value) {
    return value != nullptr && cJSON_IsNumber(value) &&
        value->valuedouble == static_cast<double>(value->valueint);
}

bool IsString(const cJSON* value) {
    return value != nullptr && cJSON_IsString(value) &&
        value->valuestring != nullptr;
}

bool ParsePoint(const cJSON* value, StackChanExpressionPoint& point) {
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

void AddPoint(cJSON* array, const StackChanExpressionPoint& point) {
    cJSON* encoded = cJSON_CreateArray();
    cJSON_AddItemToArray(encoded, cJSON_CreateNumber(point.yaw));
    cJSON_AddItemToArray(encoded, cJSON_CreateNumber(point.pitch));
    cJSON_AddItemToArray(array, encoded);
}

void AddPointValues(cJSON* array, const StackChanExpressionPoint& point) {
    cJSON_AddItemToArray(array, cJSON_CreateNumber(point.yaw));
    cJSON_AddItemToArray(array, cJSON_CreateNumber(point.pitch));
}
#endif

bool ValidateCurveVelocity(
        const StackChanExpressionStep& step,
        bool yaw,
        std::string& error) {
    std::array<int, 4> points;
    for (size_t index = 0; index < points.size(); ++index) {
        const int value = yaw
            ? step.points[index].yaw : step.points[index].pitch;
        points[index] = value * kVelocityValidationUnitsPerDegree;
    }
    uint32_t elapsed_ms = 0;
    int previous = EvaluateStackChanExpressionCurve(
        points, static_cast<uint32_t>(step.duration_ms), 0);
    const uint32_t duration_ms = static_cast<uint32_t>(step.duration_ms);
    while (elapsed_ms < duration_ms) {
        const uint32_t next_ms =
            NextStackChanExpressionSampleElapsedMs(
                elapsed_ms, duration_ms);
        const int next = EvaluateStackChanExpressionCurve(
            points, duration_ms, next_ms);
        if (!IsStackChanExpressionVelocitySafe(
                previous,
                next,
                next_ms - elapsed_ms,
                kStackChanExpressionMaxSpeedDps,
                kVelocityValidationUnitsPerDegree)) {
            error = yaw
                ? "curve exceeds the yaw velocity limit"
                : "curve exceeds the pitch velocity limit";
            return false;
        }
        previous = next;
        elapsed_ms = next_ms;
    }
    return true;
}

}  // namespace

bool IsStackChanExpressionName(const char* name) {
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

bool IsStackChanExpressionName(const std::string& name) {
    return IsStackChanExpressionName(name.c_str());
}

bool IsStackChanExpressionRecipeName(const char* name) {
    return IsStackChanExpressionName(name) ||
        (name != nullptr && std::strcmp(name, "touch") == 0);
}

bool IsStackChanExpressionRecipeName(const std::string& name) {
    return IsStackChanExpressionRecipeName(name.c_str());
}

const char* StackChanExpressionOutcomeName(StackChanExpressionOutcome outcome) {
    switch (outcome) {
        case StackChanExpressionOutcome::STARTED:
            return "started";
        case StackChanExpressionOutcome::COMPLETED:
            return "completed";
        case StackChanExpressionOutcome::BUSY:
            return "busy";
        case StackChanExpressionOutcome::INVALID_RECIPE:
            return "invalid_recipe";
        case StackChanExpressionOutcome::INTERRUPTED:
            return "interrupted";
        case StackChanExpressionOutcome::MOTION_FAILED:
            return "motion_failed";
        case StackChanExpressionOutcome::SAFE_RETURN_FAILED:
            return "safe_return_failed";
        case StackChanExpressionOutcome::UNAVAILABLE:
            return "unavailable";
        case StackChanExpressionOutcome::UNSUPPORTED_DRIVER:
            return "unsupported_driver";
    }
    return "unavailable";
}

#ifndef STACKCHAN_EXPRESSION_HOST_TEST
bool ParseStackChanExpressionRecipe(
        const cJSON* value,
        StackChanExpressionRecipe& recipe) {
    if (!cJSON_IsObject(value)) {
        return false;
    }
    const cJSON* schema =
        cJSON_GetObjectItemCaseSensitive(value, "schema_version");
    const cJSON* animations =
        cJSON_GetObjectItemCaseSensitive(value, "animations");
    const cJSON* steps = cJSON_GetObjectItemCaseSensitive(value, "steps");
    if (!IsInteger(schema) || schema->valueint != 2 ||
        !cJSON_IsArray(animations) || !cJSON_IsArray(steps)) {
        return false;
    }
    const int animation_count = cJSON_GetArraySize(animations);
    if (animation_count != 1) {
        return false;
    }
    const int count = cJSON_GetArraySize(steps);
    if (count <= 0 ||
        count > static_cast<int>(kStackChanExpressionMaxSteps)) {
        return false;
    }

    recipe = StackChanExpressionRecipe{};
    recipe.schema_version = schema->valueint;
    const cJSON* animation = cJSON_GetArrayItem(animations, 0);
    if (!IsString(animation) ||
        !IsStackChanExpressionName(animation->valuestring)) {
        return false;
    }
    recipe.animation = animation->valuestring;
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
        const cJSON* end =
            cJSON_GetObjectItemCaseSensitive(item, "end");
        if (!cJSON_IsArray(via) || cJSON_GetArraySize(via) != 2 ||
            !ParsePoint(start, step.points[0]) ||
            !ParsePoint(cJSON_GetArrayItem(via, 0), step.points[1]) ||
            !ParsePoint(cJSON_GetArrayItem(via, 1), step.points[2]) ||
            !ParsePoint(end, step.points[3])) {
            return false;
        }
    }
    return true;
}

cJSON* EncodeStackChanExpressionRecipe(
        const StackChanExpressionRecipe& recipe) {
    cJSON* encoded = cJSON_CreateObject();
    cJSON_AddNumberToObject(
        encoded, "schema_version", recipe.schema_version);
    cJSON* animations = cJSON_AddArrayToObject(encoded, "animations");
    cJSON_AddItemToArray(
        animations, cJSON_CreateString(recipe.animation.c_str()));
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
            AddPointValues(start, step.points[0]);
            cJSON* via = cJSON_AddArrayToObject(item, "via");
            AddPoint(via, step.points[1]);
            AddPoint(via, step.points[2]);
            cJSON* end = cJSON_AddArrayToObject(item, "end");
            AddPointValues(end, step.points[3]);
        }
        cJSON_AddItemToArray(steps, item);
    }
    return encoded;
}
#endif

bool ValidateStackChanExpressionRecipe(
        const StackChanExpressionRecipe& recipe,
        std::string& error) {
    if (recipe.schema_version != 2 ||
        !IsStackChanExpressionName(recipe.animation) ||
        recipe.step_count == 0 ||
        recipe.step_count > kStackChanExpressionMaxSteps) {
        error = "schema 2 currently requires one animation and one to five steps";
        return false;
    }

    StackChanExpressionPoint previous = {
        kStackChanExpressionIdleYaw,
        kStackChanExpressionIdlePitch,
    };
    uint32_t total_ms = 0;
    bool previous_was_pause = false;
    for (size_t index = 0; index < recipe.step_count; ++index) {
        const auto& step = recipe.steps[index];
        if (step.type == StackChanExpressionStepType::PAUSE) {
            if (index == 0 || index + 1 == recipe.step_count ||
                previous_was_pause || step.duration_ms <= 0 ||
                step.duration_ms > kMaximumStepDurationMs) {
                error = "pause must be bounded and between curves";
                return false;
            }
            total_ms += static_cast<uint32_t>(step.duration_ms);
            previous_was_pause = true;
            continue;
        }

        if (step.duration_ms < kMinimumCurveDurationMs ||
            step.duration_ms > kMaximumStepDurationMs) {
            error = "curve duration must be between 100 and 5000 ms";
            return false;
        }
        bool curve_moves = false;
        for (const auto& point : step.points) {
            if (point.yaw < kMinimumYaw || point.yaw > kMaximumYaw ||
                point.pitch < kMinimumPitch ||
                point.pitch > kMaximumPitch) {
                error = "curve point exceeds servo limits";
                return false;
            }
            curve_moves = curve_moves ||
                point.yaw != step.points[0].yaw ||
                point.pitch != step.points[0].pitch;
        }
        if (!curve_moves) {
            error = "curve must move at least one axis";
            return false;
        }
        if (step.points[0].yaw != previous.yaw ||
            step.points[0].pitch != previous.pitch) {
            error = "curve does not continue from the previous endpoint";
            return false;
        }
        if (!ValidateCurveVelocity(step, true, error) ||
            !ValidateCurveVelocity(step, false, error)) {
            return false;
        }
        previous = step.points[3];
        total_ms += static_cast<uint32_t>(step.duration_ms);
        previous_was_pause = false;
    }

    if (previous.yaw != kStackChanExpressionIdleYaw ||
        previous.pitch != kStackChanExpressionIdlePitch) {
        error = "recipe must return to the idle pose";
        return false;
    }
    if (total_ms > kStackChanExpressionMaxRecipeDurationMs) {
        error = "recipe exceeds the 15 second movement budget";
        return false;
    }
    return true;
}

bool ValidateStackChanExpressionRecipeForName(
        const std::string& name,
        const StackChanExpressionRecipe& recipe,
        std::string& error) {
    if (!IsStackChanExpressionRecipeName(name)) {
        error = "unknown expression";
        return false;
    }
    if (!ValidateStackChanExpressionRecipe(recipe, error)) {
        return false;
    }
    if (IsStackChanExpressionName(name) && recipe.animation != name) {
        error = "named expression animation must match its name";
        return false;
    }
    return true;
}

uint32_t StackChanExpressionRecipeDurationMs(
        const StackChanExpressionRecipe& recipe) {
    uint32_t total_ms = 0;
    for (size_t index = 0; index < recipe.step_count; ++index) {
        total_ms += static_cast<uint32_t>(recipe.steps[index].duration_ms);
    }
    return total_ms;
}

uint32_t NextStackChanExpressionSampleElapsedMs(
        uint32_t elapsed_ms,
        uint32_t duration_ms) {
    if (elapsed_ms >= duration_ms) {
        return duration_ms;
    }
    return elapsed_ms + std::min(
        kStackChanExpressionSampleIntervalMs,
        duration_ms - elapsed_ms);
}

int EvaluateStackChanExpressionAxis(
        const StackChanExpressionStep& step,
        bool yaw,
        uint32_t elapsed_ms) {
    std::array<int, 4> points;
    for (size_t index = 0; index < points.size(); ++index) {
        points[index] = yaw
            ? step.points[index].yaw : step.points[index].pitch;
    }
    return EvaluateStackChanExpressionCurve(
        points, static_cast<uint32_t>(step.duration_ms), elapsed_ms);
}

int EvaluateStackChanExpressionCurve(
        const std::array<int, 4>& points,
        uint32_t duration_ms,
        uint32_t elapsed_ms) {
    if (duration_ms == 0 || elapsed_ms >= duration_ms) {
        return points[3];
    }
    const float progress = static_cast<float>(elapsed_ms) /
        static_cast<float>(duration_ms);
    const float t = progress * progress * progress *
        (progress * (progress * 6.0f - 15.0f) + 10.0f);
    const float inverse = 1.0f - t;
    const float value = inverse * inverse * inverse * points[0] +
        3.0f * inverse * inverse * t * points[1] +
        3.0f * inverse * t * t * points[2] +
        t * t * t * points[3];
    return static_cast<int>(std::lround(value));
}

bool IsStackChanExpressionVelocitySafe(
        int previous_target,
        int next_target,
        uint32_t command_window_ms,
        int maximum_speed_dps,
        int units_per_degree) {
    if (command_window_ms == 0 || maximum_speed_dps <= 0 ||
        units_per_degree <= 0) {
        return false;
    }
    const int64_t delta = static_cast<int64_t>(next_target) -
        static_cast<int64_t>(previous_target);
    return std::llabs(delta) * 1000LL <=
        static_cast<int64_t>(maximum_speed_dps) * units_per_degree *
            command_window_ms;
}

#ifndef STACKCHAN_EXPRESSION_HOST_TEST
StackChanExpressionLoadStatus LoadStackChanExpressionRecipe(
        const std::string& name,
        StackChanExpressionRecipe& recipe) {
    if (!IsStackChanExpressionRecipeName(name)) {
        return StackChanExpressionLoadStatus::INVALID;
    }
    Settings settings(kExpressionSettingsNamespace, false);
    const std::string encoded = settings.GetString(name);
    if (encoded.empty()) {
        return StackChanExpressionLoadStatus::NOT_CALIBRATED;
    }
    cJSON* stored = cJSON_Parse(encoded.c_str());
    std::string error;
    bool valid = ParseStackChanExpressionRecipe(stored, recipe);
    if (!valid && cJSON_IsObject(stored)) {
        cJSON* schema = cJSON_GetObjectItemCaseSensitive(
            stored, "schema_version");
        if (IsInteger(schema) && schema->valueint == 1) {
            cJSON_ReplaceItemInObjectCaseSensitive(
                stored, "schema_version", cJSON_CreateNumber(2));
            cJSON* animations = cJSON_CreateArray();
            cJSON_AddItemToArray(
                animations, cJSON_CreateString(name.c_str()));
            cJSON_AddItemToObject(stored, "animations", animations);
            valid = ParseStackChanExpressionRecipe(stored, recipe);
        }
    }
    valid = valid && ValidateStackChanExpressionRecipeForName(
        name, recipe, error);
    cJSON_Delete(stored);
    return valid
        ? StackChanExpressionLoadStatus::OK
        : StackChanExpressionLoadStatus::INVALID;
}

bool SaveStackChanExpressionRecipe(
        const std::string& name,
        const StackChanExpressionRecipe& recipe,
        std::string& error) {
    if (!ValidateStackChanExpressionRecipeForName(name, recipe, error)) {
        return false;
    }
    cJSON* stored = EncodeStackChanExpressionRecipe(recipe);
    char* encoded = cJSON_PrintUnformatted(stored);
    cJSON_Delete(stored);
    if (encoded == nullptr) {
        error = "could not encode expression recipe";
        return false;
    }
    Settings settings(kExpressionSettingsNamespace, true);
    settings.SetString(name, encoded);
    cJSON_free(encoded);
    return true;
}

void ResetStackChanExpressionRecipe(const std::string& name) {
    if (!IsStackChanExpressionRecipeName(name)) {
        return;
    }
    Settings settings(kExpressionSettingsNamespace, true);
    settings.EraseKey(name);
}
#endif
