# Acknowledgements

Community work integrated into this plugin, with thanks:

## Agent Monitor — the 3.0 milestone

A huge thank-you to [@Asimatasert](https://github.com/Asimatasert) for the
[Agent Monitor proposal (#83)](https://github.com/rarf/hermes-quota-plugin/issues/83)
and implementation from [agent-monitor](https://github.com/Asimatasert/agent-monitor).
The contribution brings live session views, quota history and forecasts,
health and usage views, context guard, status-bar indicators, localization and
extensive synthetic tests into the plugin, without a separate dashboard server.
Thanks also for the follow-up YAML-loader fix in
[PR #84](https://github.com/rarf/hermes-quota-plugin/pull/84).

The 3.0 integration preserves maintainer-side opt-in and profile-scoping
hardening. Some capabilities from the original proposal remain deliberately
unavailable; see [the shipped monitor contract](monitor.md).

## Provider and platform contributions

- [@d31tcjg](https://github.com/d31tcjg) for Chrome cookie import for the Grok
  provider (#2)
- [@tdoan35](https://github.com/tdoan35) for the Z.ai Coding Plan provider, the
  gateway-diagnostics JSON fix and the Codex usage-URL helper fix (#12, #13, #14)
- [@Hyp4tia](https://github.com/Hyp4tia) for the CommandCode provider (#15)
- [@mredodos](https://github.com/mredodos) for resolving Hermes-managed
  credentials and honest unavailable reasons for Kimi and Anthropic (#19)
- [@donovan-yohan](https://github.com/donovan-yohan) for the Cursor provider and
  Anthropic's model-scoped weekly limits (#20, #21)
- [@jfjauvin](https://github.com/jfjauvin) for routing quota commands to the
  focused profile (#26)
- [@spiwar](https://github.com/spiwar) for the Antigravity provider and its
  bucket semantics (#25, landed in #31)
- [@lanx214](https://github.com/lanx214) for the CommandCode credential report
  and the live re-validation that cleared it (#27)
- [@zaydiscold](https://github.com/zaydiscold) for DeepSeek account balances
  with the pane's balance layout, and OpenRouter per-key quotas with a scoped
  account wallet (#23, #24)
- [@thileman](https://github.com/thileman) for the MiniMax Token Plan fetcher
  (#29, #30)

MIT.
