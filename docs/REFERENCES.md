# References

## Pinned Source Reference

### stackchan-mcp

- Repository: <https://github.com/kisaragi-mochi/stackchan-mcp>
- Local path: `references/stackchan-mcp`
- Pinned gitlink: `804af573ba8f577f63efbd39f6e8a9c7f57b4647`
- Local checkout state: uninitialized
- Upstream license: MIT License (as recorded at the pinned revision).
- Role: temporary study source for firmware, gateway behavior, hardware
  vocabulary, safety, and reconnect behavior. It is never a runtime dependency
  or vendored implementation.

Capabilities relevant to Milestone 1 include device status, head movement,
avatar-name selection, blinking, mouth control, LEDs, gateway authentication,
mDNS discovery, reconnect handling, and physical event notification.

Avatar source evidence at pinned revision
`804af573ba8f577f63efbd39f6e8a9c7f57b4647` and tag
`firmware-v1.16.0` is explicit: `avatar_images.cc` is marked as a placeholder,
and each of its 14 static image descriptors contains two bytes representing a
1x1 black pixel. An `ok=true` response can therefore confirm LVGL application
of the selected symbol without proving a human-visible display change. Real
assets must be installed and each enabled face must be confirmed by human
observation before semantic use.

Known upstream cautions to verify against the pinned revision:

- Large abrupt head reversals may stress or hang the servo bus.
- The recommended pitch operating range is narrower than the firmware hard
  clamp.
- Touch events may occasionally be dropped; touch is outside Milestone 1.
- The referenced release avatar symbols are placeholder 1x1 black assets, not
  visible expression artwork.

The implementation must not import or execute this submodule. Treat it as
read-only.

**Removal gate:** after every required Milestone 1 gateway and device behavior
has been adapted and verified locally, remove the submodule gitlink,
`.gitmodules`, and the empty `references/` directory. Until then, retain the
pinned study source and attribution.

## Official Hardware and Firmware

- M5Stack StackChan documentation: <https://docs.m5stack.com/en/StackChan>
- Official open-source repository: <https://github.com/m5stack/StackChan>
- Original community project: <https://github.com/stack-chan/stack-chan>

## OpenClaw Integration Documentation

- MCP management: <https://docs.openclaw.ai/cli/mcp>
- Plugin SDK overview: <https://docs.openclaw.ai/plugins/sdk-overview>
- Tool plugins: <https://docs.openclaw.ai/plugins/tool-plugins>
- Scheduled tasks: <https://docs.openclaw.ai/automation/cron-jobs>
- Webhooks: <https://docs.openclaw.ai/webhook>
- External integrations: <https://docs.openclaw.ai/gateway/external-apps>

## Inspiration, Not Dependencies

- Stackchan Alive: <https://github.com/RobVanProd/stackchan_alive>
- StackChan Matchday: <https://github.com/xymeow/stackchan-matchday>
- Pet-like firmware: <https://github.com/Corvelis/stackchan-pet-fw>
- Dotty: <https://github.com/BrettKinny/dotty-stackchan>
- Warble local voice backend: <https://github.com/rebelthor/warble>
- AIAvatarStackChan: <https://github.com/uezo/AIAvatarStackChan>
- Simple StackChan HTTP API: <https://github.com/zziying/stackchan-openapi>
