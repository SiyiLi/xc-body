import assert from "node:assert/strict";
import test from "node:test";

import { prepareDirectAnswer } from "../direct-conversation.ts";
import { EXPRESSION_NAMES } from "../expression.ts";
import {
  needsSpeechProjection,
  parseDirectProjection,
  parseSpokenProjection,
  prepareDirectSpeech,
  projectSpokenText,
} from "../spoken-text.ts";

test("plain answers keep exact speech and select from the answer", async () => {
  const answer = "不，我不同意。";
  let calls = 0;
  const prepared = await prepareDirectSpeech(async (params) => {
    calls += 1;
    assert.deepEqual(JSON.parse(params.messages[0]?.content ?? ""), {
      openclaw_answer: answer,
      project_speech: false,
    });
    return {
      text: JSON.stringify({ speech: null, expression: "concerned" }),
    };
  }, answer);
  assert.deepEqual(prepared, {
    speech: answer,
    expression: "concerned",
  });
  assert.equal(calls, 1);
  assert.equal(needsSpeechProjection("word ".repeat(200)), false);
  assert.equal(needsSpeechProjection("word ".repeat(201)), true);
  assert.equal(needsSpeechProjection("a".repeat(1_000)), false);
  assert.equal(needsSpeechProjection("a".repeat(1_001)), true);
});

test("plain answers survive expression projection failure", async () => {
  const answer = "十五。";
  let calls = 0;
  const prepared = await prepareDirectSpeech(async () => {
    calls += 1;
    throw new Error("projection unavailable");
  }, answer);

  assert.deepEqual(prepared, { speech: answer, expression: "idle" });
  assert.equal(calls, 2);
});

test("long-answer projection selects from the final answer alone", async () => {
  const answer = [
    "| 城市 | 天气 |",
    "| --- | --- |",
    "| 上海 | 有雨 |",
  ].join("\n");
  assert.equal(needsSpeechProjection(answer), true);

  const prepared = await prepareDirectSpeech(
    async (params) => {
      assert.deepEqual(JSON.parse(params.messages[0]?.content ?? ""), {
        openclaw_answer: answer,
        project_speech: true,
      });
      for (const expression of EXPRESSION_NAMES) {
        assert.match(params.systemPrompt, new RegExp(`\\b${expression}:`));
      }
      return {
        text: JSON.stringify({
          speech: "上海今天有雨。",
          expression: "concerned",
        }),
      };
    },
    answer,
  );

  assert.deepEqual(prepared, {
    speech: "上海今天有雨。",
    expression: "concerned",
  });
});

test("direct caller falls back to error speech and idle", async () => {
  let calls = 0;
  const prepared = await prepareDirectAnswer(async () => {
    calls += 1;
    return { text: "not valid JSON" };
  }, "# not suitable for speech");

  assert.deepEqual(prepared, {
    speech: "抱歉，在生成最终答案时出了点问题。",
    expression: "idle",
  });
  assert.equal(calls, 2);
});

test("background projection retries invalid structured output", async () => {
  let calls = 0;
  const projection = await projectSpokenText(async (params) => {
    calls += 1;
    for (const expression of EXPRESSION_NAMES) {
      assert.match(params.systemPrompt, new RegExp(`\\b${expression}:`));
    }
    assert.deepEqual(JSON.parse(params.messages[0]?.content ?? ""), {
      openclaw_result: "completed result",
    });
    return calls === 1
      ? { text: "not valid JSON" }
      : {
          text: JSON.stringify({
            decision: "skip",
            speech: null,
            expression: null,
          }),
        };
  }, "completed result");

  assert.deepEqual(projection, {
    decision: "skip",
    speech: "",
    expression: null,
  });
  assert.equal(calls, 2);
});

test("projection parsers enforce speech and expression boundaries", () => {
  assert.deepEqual(
    parseSpokenProjection(JSON.stringify({
      decision: "offer",
      speech: "任务已经完成。",
      expression: "pleased",
    })),
    {
      decision: "offer",
      speech: "任务已经完成。",
      expression: "pleased",
    },
  );
  assert.deepEqual(
    parseSpokenProjection(JSON.stringify({
      decision: "skip",
      speech: null,
      expression: null,
    })),
    { decision: "skip", speech: "", expression: null },
  );
  assert.deepEqual(
    parseDirectProjection(JSON.stringify({
      speech: "我不太确定。",
      expression: "curious",
    }), true),
    { speech: "我不太确定。", expression: "curious" },
  );
  assert.deepEqual(
    parseDirectProjection(JSON.stringify({
      speech: null,
      expression: "agree",
    }), false),
    { speech: null, expression: "agree" },
  );
  for (const invalid of [
    JSON.stringify({
      decision: "offer",
      speech: "任务已经完成。",
      expression: "idle",
    }),
    JSON.stringify({
      decision: "offer",
      speech: "English only",
      expression: "pleased",
    }),
    JSON.stringify({
      speech: "任务已经完成。",
      expression: "unknown",
    }),
  ]) {
    assert.equal(parseSpokenProjection(invalid), null);
  }
});
