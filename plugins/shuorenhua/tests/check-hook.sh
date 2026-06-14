#!/bin/sh
set -eu

plugin_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
tmpdir=$(mktemp -d)
trap 'rm -rf "$tmpdir"' EXIT

printf '%s\n' '{"session_id":"sid","turn_id":"tid","transcript_path":"/tmp/session.jsonl","cwd":"/tmp","hook_event_name":"UserPromptSubmit","model":"gpt-5.5","permission_mode":"default","prompt":"translate"}' \
  | PLUGIN_DATA="$tmpdir" python3 "$plugin_root/scripts/session_bridge.py" capture-hook > "$tmpdir/non-command.out"

test ! -s "$tmpdir/non-command.out"

printf '%s\n' '{"session_id":"sid","turn_id":"tid","transcript_path":"/tmp/session.jsonl","cwd":"/tmp","hook_event_name":"UserPromptSubmit","model":"gpt-5.5","permission_mode":"default","prompt":"$srh"}' \
  | PLUGIN_DATA="$tmpdir" python3 "$plugin_root/scripts/session_bridge.py" capture-hook > "$tmpdir/dollar-command.out"

test ! -s "$tmpdir/dollar-command.out"

node -e "
const fs = require('fs');
const path = '$tmpdir/state/last_hook_input.json';
const payload = JSON.parse(fs.readFileSync(path, 'utf8'));
if (payload.session_id !== 'sid') throw new Error('missing session_id');
if (payload.turn_id !== 'tid') throw new Error('missing turn_id');
if (payload.transcript_path !== '/tmp/session.jsonl') throw new Error('missing transcript_path');
console.log('Hook capture valid');
"

session="$tmpdir/rollout-2026-01-01T00-00-00-00000000-0000-0000-0000-000000000000.jsonl"
cat > "$session" <<'JSONL'
{"type":"session_meta","timestamp":"2026-01-01T00:00:00Z","payload":{"id":"00000000-0000-0000-0000-000000000000","cwd":"/repo"}}
{"type":"response_item","timestamp":"2026-01-01T00:00:00Z","payload":{"type":"agent_message","message":"Old assistant message.","phase":"final"}}
{"type":"response_item","timestamp":"2026-01-01T00:00:01Z","payload":{"type":"user_message","message":"Translate the last answer."}}
{"type":"response_item","timestamp":"2026-01-01T00:00:02Z","payload":{"type":"agent_message","message":"Run tests before installing.","phase":"final"}}
{"type":"response_item","timestamp":"2026-01-01T00:00:03Z","payload":{"type":"user_message","message":"srh"}}
{"type":"response_item","timestamp":"2026-01-01T00:00:04Z","payload":{"type":"agent_message","message":"What would you like me to do? \"srh\" is not enough context.","phase":"final"}}
{"type":"response_item","timestamp":"2026-01-01T00:00:05Z","payload":{"type":"user_message","message":"<name>shuorenhua:srh</name>"}}
{"type":"response_item","timestamp":"2026-01-01T00:00:06Z","payload":{"type":"agent_message","message":"`--last-hook` had no available record.","phase":"final"}}
JSONL

mkdir -p "$tmpdir/bin"
cat > "$tmpdir/bin/claude" <<'SH'
#!/bin/sh
cat > "$SRH_FAKE_STDIN"
printf '译文\n'
SH
chmod 0755 "$tmpdir/bin/claude"

payload=$(printf '{"session_id":"sid","turn_id":"tid2","transcript_path":"%s","cwd":"%s","hook_event_name":"UserPromptSubmit","model":"gpt-5.5","permission_mode":"default","prompt":"srh --timeout 5"}' "$session" "$tmpdir")
printf '%s\n' "$payload" \
  | PATH="$tmpdir/bin:$PATH" SRH_FAKE_STDIN="$tmpdir/claude.stdin" PLUGIN_DATA="$tmpdir" \
    python3 "$plugin_root/scripts/session_bridge.py" capture-hook > "$tmpdir/hook-command.out"

node -e "
const fs = require('fs');
const result = JSON.parse(fs.readFileSync('$tmpdir/hook-command.out', 'utf8'));
if (result.decision !== 'block') throw new Error('hook command did not block');
if (!result.reason.includes('译文')) throw new Error('missing translated reason');
const input = fs.readFileSync('$tmpdir/claude.stdin', 'utf8');
if (!input.includes('Context: User Input')) throw new Error('missing user context');
if (!input.includes('Assistant Message To Translate')) throw new Error('missing assistant section');
if (!input.includes('Translate the last answer.')) throw new Error('missing preceding user message');
if (!input.includes('Run tests before installing.')) throw new Error('missing latest assistant message');
if (input.includes('Old assistant message.')) throw new Error('old assistant message leaked');
if (input.includes('not enough context')) throw new Error('srh command failure leaked');
if (input.includes('--last-hook')) throw new Error('legacy srh skill output leaked');
"
