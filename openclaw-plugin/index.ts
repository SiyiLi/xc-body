import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";

import {
  CompletionIntegration,
  parsePluginConfig,
  submitSummary,
} from "./core.ts";
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
    });
    registerCompletionHooks(api, integration);
  },
});
