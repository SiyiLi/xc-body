import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";

import {
  CompletionIntegration,
  parsePluginConfig,
  submitSummary,
} from "./core.ts";
import { DirectConversationService } from "./direct-conversation.ts";
import { registerCompletionHooks } from "./hooks.ts";

export default definePluginEntry({
  id: "xc-body-native",
  name: "XC Body Native Integration",
  description: "Offers selected background completions through XC Body.",
  register(api) {
    let config: ReturnType<typeof parsePluginConfig>;
    try {
      config = parsePluginConfig(api.pluginConfig ?? {});
    } catch {
      api.logger.warn(
        "XC Body native integration is disabled by invalid configuration",
      );
      return;
    }
    const integration = new CompletionIntegration({
      complete: (params) => api.runtime.llm.complete(params),
      submit: (payload) => submitSummary(config, payload),
      model: config.speechModel,
    });
    if (
      config.voiceUrl &&
      config.sessionKey &&
      config.telegramTarget
    ) {
      const directService = new DirectConversationService(api, {
        voiceUrl: config.voiceUrl,
        token: config.token,
        sessionKey: config.sessionKey,
        telegramTarget: config.telegramTarget,
        agentId: config.agentId,
        complete: (params) => api.runtime.llm.complete(params),
        speechModel: config.speechModel,
        pollMs: config.pollMs,
        timeoutMs: config.timeoutMs,
      });
      api.registerService({
        id: "xc-body-direct-conversation",
        start() {
          directService.start();
        },
        async stop() {
          await directService.stop();
        },
      });
    }
    registerCompletionHooks(api, integration);
  },
});
