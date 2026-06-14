# codex-plugin-shuorenhua

Hook-only Codex plugin for translating the latest Codex assistant message into Chinese with another local coding-agent CLI. It exports the latest assistant reply, includes the preceding user input as context, and returns the translation through the `UserPromptSubmit` hook.

The plugin lives at `plugins/shuorenhua/`.

## Install

Add this repo as a Codex marketplace:

```bash
codex plugin marketplace add StarDuster/codex-plugin-shuorenhua --ref main
```

Install the plugin:

```bash
codex plugin add shuorenhua@stardust-plugins
```

Start a new Codex thread, open `/hooks`, review the `shuorenhua` hook, and trust it.

## Use

Type one of these at the Codex prompt:

```text
srh
shuorenhua
说人话
```

Examples:

```text
srh --agent claude --model sonnet
srh --agent opencode --model google/gemini-3-pro
说人话 --agent codex --model gpt-5.4-mini
```

## Behavior

The `UserPromptSubmit` hook runs `plugins/shuorenhua/scripts/session_bridge.py capture-hook`.

For ordinary prompts, it records hook metadata. For `srh`, `shuorenhua`, or `说人话`, it:

1. Reads the current Codex `transcript_path`.
2. Exports the preceding user input as context.
3. Exports the latest assistant message as the text to translate.
4. Calls the selected local backend.
5. Returns the translated text as the hook block reason.

The exported payload is scoped to the latest assistant message plus the preceding user input.

## Backends

- `claude`: Claude Code through `claude --safe-mode -p`.
- `opencode`: OpenCode through `opencode run`.
- `omp`: Oh My Pi through `omp -p`.
- `agy`: Google Antigravity through `agy --print`.
- `codex`: another Codex run through `codex exec`.

## Development

Export a transcript payload:

```bash
python3 plugins/shuorenhua/scripts/session_bridge.py export --latest --out .shuorenhua/session.md
```

Ask a backend:

```bash
python3 plugins/shuorenhua/scripts/session_bridge.py ask --agent claude --model sonnet --input .shuorenhua/session.md --out .shuorenhua/translation.md
```

Dry-run a backend command:

```bash
python3 plugins/shuorenhua/scripts/session_bridge.py ask --agent claude --model sonnet --latest --dry-run
```

## Files

- `plugins/shuorenhua/hooks/hooks.json`: Codex hook configuration.
- `plugins/shuorenhua/scripts/session_bridge.py`: transcript export, hook handling, and backend adapters.
- `.agents/plugins/marketplace.json`: marketplace catalog.

## Verify

```bash
python3 -m py_compile plugins/shuorenhua/scripts/session_bridge.py
/bin/sh plugins/shuorenhua/tests/check-hook.sh
/bin/sh plugins/shuorenhua/tests/check-session-bridge.sh
/bin/sh plugins/shuorenhua/tests/check-style-contract.sh
python3 /Users/stardust/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py plugins/shuorenhua
```

## Update Installed Marketplace

After new commits are pushed:

```bash
codex plugin marketplace upgrade stardust-plugins
codex plugin add shuorenhua@stardust-plugins
```

Start a new Codex thread after reinstalling.

## License

MIT
