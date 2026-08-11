# References

## Pinned Source Reference

### stackchan-mcp

- Repository: <https://github.com/kisaragi-mochi/stackchan-mcp>
- Local path: `references/stackchan-mcp`
- Pinned gitlink: `804af573ba8f577f63efbd39f6e8a9c7f57b4647`
- Local checkout state: uninitialized
- Role: initial firmware, device gateway, MCP hardware vocabulary, safety and
  reconnect reference for the official K151/CoreS3 kit.

Capabilities relevant to Milestone 1 include device status, head movement,
avatar selection, blinking, mouth control, LEDs, gateway authentication, mDNS
discovery, reconnect handling, and physical event notification.

Known upstream cautions to verify against the pinned revision:

- Large abrupt head reversals may stress or hang the servo bus.
- The recommended pitch operating range is narrower than the firmware hard
  clamp.
- Touch events may occasionally be dropped; touch is outside Milestone 1.
- Personal avatar assets are intentionally kept out of the upstream repository.

Treat this submodule as read-only until a concrete upstream limitation is
demonstrated.

## Official Hardware and Firmware

- M5Stack StackChan documentation: <https://docs.m5stack.com/en/StackChan>
- Official open-source repository: <https://github.com/m5stack/StackChan>
- Original community Stack-chan project: <https://github.com/stack-chan/stack-chan>

## OpenClaw Integration Documentation

- MCP management: <https://docs.openclaw.ai/cli/mcp>
- Plugin SDK overview: <https://docs.openclaw.ai/plugins/sdk-overview>
- Tool plugins: <https://docs.openclaw.ai/plugins/tool-plugins>
- Scheduled tasks: <https://docs.openclaw.ai/automation/cron-jobs>
- Webhooks: <https://docs.openclaw.ai/webhook>
- External Gateway integrations: <https://docs.openclaw.ai/gateway/external-apps>

## Inspiration, Not Dependencies

- Stackchan Alive: <https://github.com/RobVanProd/stackchan_alive>
- StackChan Matchday: <https://github.com/xymeow/stackchan-matchday>
- Pet-like firmware: <https://github.com/Corvelis/stackchan-pet-fw>
- Dotty: <https://github.com/BrettKinny/dotty-stackchan>
- Warble local voice backend: <https://github.com/rebelthor/warble>
- AIAvatarStackChan: <https://github.com/uezo/AIAvatarStackChan>
- Simple StackChan HTTP API: <https://github.com/zziying/stackchan-openapi>
