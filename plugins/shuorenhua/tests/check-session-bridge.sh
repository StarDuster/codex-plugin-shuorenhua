#!/bin/sh
set -eu

plugin_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
tmpdir=$(mktemp -d)
trap 'rm -rf "$tmpdir"' EXIT

session="$tmpdir/rollout-2026-01-01T00-00-00-00000000-0000-0000-0000-000000000000.jsonl"
cat > "$session" <<'JSONL'
{"type":"session_meta","timestamp":"2026-01-01T00:00:00Z","payload":{"id":"00000000-0000-0000-0000-000000000000","cwd":"/repo"}}
{"type":"response_item","timestamp":"2026-01-01T00:00:00Z","payload":{"type":"agent_message","message":"Old assistant message.","phase":"final"}}
{"type":"response_item","timestamp":"2026-01-01T00:00:01Z","payload":{"type":"user_message","message":"Translate this session."}}
{"type":"response_item","timestamp":"2026-01-01T00:00:02Z","payload":{"type":"reasoning","summary":[{"type":"summary_text","text":"Need inspect transcript."}]}}
{"type":"response_item","timestamp":"2026-01-01T00:00:03Z","payload":{"type":"agent_message","message":"I will export the transcript.","phase":"commentary"}}
{"type":"response_item","timestamp":"2026-01-01T00:00:04Z","payload":{"type":"user_message","message":"srh"}}
{"type":"response_item","timestamp":"2026-01-01T00:00:05Z","payload":{"type":"agent_message","message":"What would you like me to do? \"srh\" is not enough context.","phase":"final"}}
{"type":"response_item","timestamp":"2026-01-01T00:00:06Z","payload":{"type":"user_message","message":"<name>shuorenhua:srh</name>"}}
{"type":"response_item","timestamp":"2026-01-01T00:00:07Z","payload":{"type":"agent_message","message":"`--last-hook` had no available record.","phase":"final"}}
JSONL

python3 "$plugin_root/scripts/session_bridge.py" export --transcript "$session" --out "$tmpdir/out.md"
grep -q "Context: User Input" "$tmpdir/out.md"
grep -q "Assistant Message To Translate" "$tmpdir/out.md"
grep -q "Translate this session." "$tmpdir/out.md"
grep -q "I will export the transcript." "$tmpdir/out.md"
if grep -q "Need inspect transcript." "$tmpdir/out.md"; then
  echo "reasoning leaked into last-message export" >&2
  exit 1
fi
if grep -q "Old assistant message." "$tmpdir/out.md"; then
  echo "old assistant message leaked into last-message export" >&2
  exit 1
fi
if grep -q "not enough context" "$tmpdir/out.md"; then
  echo "srh command failure leaked into last-message export" >&2
  exit 1
fi
if grep -q -- "--last-hook" "$tmpdir/out.md"; then
  echo "legacy srh skill output leaked into last-message export" >&2
  exit 1
fi

python3 "$plugin_root/scripts/session_bridge.py" ask --agent claude --model sonnet --input "$tmpdir/out.md" --dry-run > "$tmpdir/claude.json"
mkdir -p "$tmpdir/codex-home/sessions/2026/01/01"
cp "$session" "$tmpdir/codex-home/sessions/2026/01/01/$(basename "$session")"
CODEX_HOME="$tmpdir/codex-home" CODEX_THREAD_ID="00000000-0000-0000-0000-000000000000" \
  python3 "$plugin_root/scripts/session_bridge.py" ask --agent claude --dry-run > "$tmpdir/thread.json"
python3 "$plugin_root/scripts/session_bridge.py" ask --agent opencode --model google/gemini-3-pro --input "$tmpdir/out.md" --dry-run > "$tmpdir/opencode.json"
python3 "$plugin_root/scripts/session_bridge.py" ask --agent omp --model gemini --input "$tmpdir/out.md" --dry-run > "$tmpdir/omp.json"
python3 "$plugin_root/scripts/session_bridge.py" ask --agent agy --input "$tmpdir/out.md" --dry-run > "$tmpdir/agy.json"

grep -q '"claude"' "$tmpdir/claude.json"
grep -q '"scope": "last-assistant"' "$tmpdir/claude.json"
grep -q '"--safe-mode"' "$tmpdir/claude.json"
grep -q '"source": "' "$tmpdir/claude.json"
if grep -q '"--bare"' "$tmpdir/claude.json"; then
  echo "claude command must not use --bare; it ignores normal Claude Code login" >&2
  exit 1
fi
grep -q '00000000-0000-0000-0000-000000000000' "$tmpdir/thread.json"
grep -q '"opencode"' "$tmpdir/opencode.json"
grep -q '"omp"' "$tmpdir/omp.json"
grep -q '"agy"' "$tmpdir/agy.json"

mkdir -p "$tmpdir/bin"
cat > "$tmpdir/bin/claude" <<'SH'
#!/bin/sh
cat >/dev/null
printf '译文\n'
SH
chmod 0755 "$tmpdir/bin/claude"

PATH="$tmpdir/bin:$PATH" python3 "$plugin_root/scripts/session_bridge.py" ask --agent claude --input "$tmpdir/out.md" > "$tmpdir/ask.out" 2> "$tmpdir/ask.err"
grep -q "译文" "$tmpdir/ask.out"
grep -q "srh: source=" "$tmpdir/ask.err"
grep -q "srh: running claude" "$tmpdir/ask.err"

cat > "$tmpdir/bin/claude" <<'SH'
#!/bin/sh
cat >/dev/null
printf 'Not logged in · Please run /login\n'
exit 1
SH
chmod 0755 "$tmpdir/bin/claude"

if PATH="$tmpdir/bin:$PATH" python3 "$plugin_root/scripts/session_bridge.py" ask --agent claude --input "$tmpdir/out.md" > "$tmpdir/auth.out" 2> "$tmpdir/auth.err"; then
  echo "auth failure unexpectedly succeeded" >&2
  exit 1
fi
grep -q "Not logged in" "$tmpdir/auth.err"
grep -q "Claude Code reported no usable auth" "$tmpdir/auth.err"

cat > "$tmpdir/bin/claude" <<'SH'
#!/bin/sh
cat >/dev/null
exit 0
SH
chmod 0755 "$tmpdir/bin/claude"

if PATH="$tmpdir/bin:$PATH" python3 "$plugin_root/scripts/session_bridge.py" ask --agent claude --input "$tmpdir/out.md" > "$tmpdir/empty.out" 2> "$tmpdir/empty.err"; then
  echo "empty external-agent output unexpectedly succeeded" >&2
  exit 1
fi
grep -q "external agent returned empty output" "$tmpdir/empty.err"

echo "Session bridge valid"
