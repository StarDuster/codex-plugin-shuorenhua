#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
from typing import Any


DEFAULT_MAX_CHARS = 220_000
DEFAULT_SCOPE = "last-assistant"
HOOK_COMMANDS = {
    "srh",
    "shuorenhua",
    "说人话",
}
LEGACY_CONTROL_TOKENS = {
    "$srh",
    "@srh",
    "$shuorenhua",
    "@shuorenhua",
}
LEGACY_CONTROL_MARKERS = (
    "shuorenhua:srh",
    "<name>shuorenhua:srh</name>",
)
COMMON_PATH_DIRS = (
    "~/.codex/bin",
    "~/.local/bin",
    "~/bin",
    "/opt/homebrew/bin",
    "/usr/local/bin",
    "/usr/bin",
    "/bin",
)


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser()


def plugin_data() -> Path:
    return Path(os.environ.get("PLUGIN_DATA", "~/.codex/shuorenhua")).expanduser()


def state_path() -> Path:
    return plugin_data() / "state" / "last_hook_input.json"


def load_json_line(line: str) -> dict[str, Any] | None:
    try:
        value = json.loads(line)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def text_from_content(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = [text_from_content(item) for item in value]
        return "\n".join(part for part in parts if part)
    if isinstance(value, dict):
        for key in ("text", "message", "summary", "content", "output"):
            if key in value:
                text = text_from_content(value[key])
                if text:
                    return text
        return ""
    return str(value)


def append_block(blocks: list[tuple[str, str]], title: str, text: str) -> None:
    clean = text.strip()
    if not clean:
        return
    if blocks and blocks[-1] == (title, clean):
        return
    blocks.append((title, clean))


def is_hook_command_text(text: str) -> bool:
    prompt = text.strip()
    if not prompt:
        return False
    try:
        first = shlex.split(prompt)[0]
    except ValueError:
        first = prompt.split(maxsplit=1)[0]
    return first in HOOK_COMMANDS


def is_srh_control_context(text: str) -> bool:
    prompt = text.strip()
    if not prompt:
        return False
    if is_hook_command_text(prompt):
        return True
    try:
        first = shlex.split(prompt)[0]
    except ValueError:
        first = prompt.split(maxsplit=1)[0]
    if first in LEGACY_CONTROL_TOKENS:
        return True
    lower_prompt = prompt.lower()
    return any(marker in lower_prompt for marker in LEGACY_CONTROL_MARKERS)


def preceding_user_index(blocks: list[tuple[str, str]], before_index: int) -> int | None:
    return next(
        (
            index
            for index in range(before_index - 1, -1, -1)
            if blocks[index][0] == "User"
        ),
        None,
    )


def assistant_has_hook_command_context(blocks: list[tuple[str, str]], assistant_index: int) -> bool:
    user_index = preceding_user_index(blocks, assistant_index)
    return user_index is not None and is_srh_control_context(blocks[user_index][1])


def preview(text: str, limit: int = 900) -> str:
    clean = text.strip()
    if len(clean) <= limit:
        return clean
    return clean[:limit].rstrip() + "\n...[truncated]"


def eprint(message: str, *, quiet: bool = False) -> None:
    if not quiet:
        print(message, file=sys.stderr, flush=True)


def export_rollout(
    transcript_path: Path,
    *,
    include_tools: bool = False,
    include_system: bool = False,
    max_chars: int = DEFAULT_MAX_CHARS,
    scope: str = DEFAULT_SCOPE,
) -> str:
    blocks: list[tuple[str, str]] = []
    meta: dict[str, Any] = {}

    with transcript_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            obj = load_json_line(line)
            if not obj:
                continue
            payload = obj.get("payload") or {}
            if not isinstance(payload, dict):
                continue

            kind = payload.get("type")
            if obj.get("type") == "session_meta":
                meta = payload
                continue

            if kind == "user_message":
                append_block(blocks, "User", text_from_content(payload.get("message")))
            elif kind == "agent_message":
                phase = payload.get("phase")
                title = f"Assistant ({phase})" if phase else "Assistant"
                append_block(blocks, title, text_from_content(payload.get("message")))
            elif kind == "reasoning":
                append_block(blocks, "Reasoning summary", text_from_content(payload.get("summary")))
            elif kind == "message":
                role = payload.get("role")
                if role == "developer" and not include_system:
                    continue
                if role in {"system", "developer"} and include_system:
                    append_block(blocks, role.title(), text_from_content(payload.get("content")))
                elif role == "user":
                    append_block(blocks, "User", text_from_content(payload.get("content")))
                elif role == "assistant":
                    append_block(blocks, "Assistant", text_from_content(payload.get("content")))
            elif include_tools and kind in {
                "function_call",
                "function_call_output",
                "custom_tool_call",
                "custom_tool_call_output",
            }:
                name = payload.get("name") or payload.get("call_id") or kind
                body = (
                    text_from_content(payload.get("arguments"))
                    or text_from_content(payload.get("output"))
                    or text_from_content(payload)
                )
                append_block(blocks, f"Tool event: {name}", preview(body))

    if scope == "last-assistant":
        assistant_index = next(
            (
                index
                for index in range(len(blocks) - 1, -1, -1)
                if blocks[index][0].startswith("Assistant")
                and not assistant_has_hook_command_context(blocks, index)
            ),
            None,
        )
        if assistant_index is None:
            raise SystemExit("no assistant message found before the srh command")
        else:
            selected: list[tuple[str, str]] = []
            user_index = preceding_user_index(blocks, assistant_index)
            if user_index is not None:
                selected.append(("Context: User Input", blocks[user_index][1]))
            selected.append(("Assistant Message To Translate", blocks[assistant_index][1]))
            blocks = selected
    else:
        raise SystemExit(f"unsupported transcript scope: {scope}")

    header = [
        "# Codex Last Assistant Message",
        "",
        f"Source: `{transcript_path}`",
        f"Scope: `{scope}`",
    ]
    session_id = meta.get("id") or session_id_from_path(transcript_path)
    if session_id:
        header.append(f"Session: `{session_id}`")
    cwd = meta.get("cwd")
    if cwd:
        header.append(f"CWD: `{cwd}`")
    header.append("")

    lines = header
    used = sum(len(line) + 1 for line in lines)
    for title, text in blocks:
        chunk = f"## {title}\n\n{text}\n"
        if used + len(chunk) > max_chars:
            lines.append("\n## Truncation\n\nTranscript export stopped at the configured character limit.\n")
            break
        lines.append(chunk)
        used += len(chunk)
    return "\n".join(lines)


def session_id_from_path(path: Path) -> str | None:
    match = re.search(
        r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
        path.name,
        re.IGNORECASE,
    )
    return match.group(1) if match else None


def latest_session() -> Path:
    root = codex_home() / "sessions"
    files = [p for p in root.rglob("rollout-*.jsonl") if p.is_file()]
    if not files:
        raise SystemExit(f"no Codex session files found under {root}")
    return max(files, key=lambda p: p.stat().st_mtime)


def find_session_by_id(session_id: str) -> Path:
    root = codex_home() / "sessions"
    matches = [p for p in root.rglob(f"*{session_id}*.jsonl") if p.is_file()]
    if not matches:
        raise SystemExit(f"no Codex session file found for session id {session_id}")
    return max(matches, key=lambda p: p.stat().st_mtime)


def load_last_hook() -> dict[str, Any]:
    path = state_path()
    if not path.exists():
        raise SystemExit(f"no captured hook input found at {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_transcript_path(args: argparse.Namespace) -> Path:
    if getattr(args, "transcript", None):
        return Path(args.transcript).expanduser()
    if getattr(args, "session_id", None):
        return find_session_by_id(args.session_id)
    if getattr(args, "last_hook", False):
        hook = load_last_hook()
        transcript = hook.get("transcript_path")
        if not transcript:
            raise SystemExit("last hook input did not include transcript_path")
        return Path(transcript).expanduser()
    if getattr(args, "latest", False):
        return latest_session()
    env_transcript = os.environ.get("SRH_TRANSCRIPT") or os.environ.get("SHUORENHUA_TRANSCRIPT")
    if env_transcript:
        return Path(env_transcript).expanduser()
    env_session_id = (
        os.environ.get("SRH_SESSION_ID")
        or os.environ.get("SHUORENHUA_SESSION_ID")
        or os.environ.get("CODEX_THREAD_ID")
        or os.environ.get("CODEX_SESSION_ID")
    )
    if env_session_id:
        return find_session_by_id(env_session_id)
    return latest_session()


def command_for_agent(agent: str, model: str | None, prompt: str, input_path: Path) -> tuple[list[str], str | None]:
    agent = agent.lower()
    transcript = input_path.read_text(encoding="utf-8")

    if agent == "claude":
        cmd = [
            "claude",
            "--safe-mode",
            "-p",
            prompt,
            "--output-format",
            "text",
            "--no-session-persistence",
            "--tools",
            "",
        ]
        if model:
            cmd.extend(["--model", model])
        return cmd, transcript

    if agent == "opencode":
        cmd = ["opencode", "run", prompt, "--file", str(input_path)]
        if model:
            cmd[2:2] = ["--model", model]
        return cmd, None

    if agent == "omp":
        cmd = ["omp", "-p", "--no-tools", "--no-session"]
        if model:
            cmd.extend(["--model", model])
        cmd.extend([f"@{input_path}", prompt])
        return cmd, None

    if agent == "agy":
        cmd = ["agy", "--print", f"{prompt}\n\n{transcript}"]
        if model:
            cmd.extend(["--model", model])
        return cmd, None

    if agent == "codex":
        cmd = ["codex", "exec", "--skip-git-repo-check", "--sandbox", "read-only", prompt]
        if model:
            cmd[2:2] = ["--model", model]
        return cmd, transcript

    raise SystemExit(f"unsupported agent: {agent}")


def default_prompt(_scope: str = DEFAULT_SCOPE) -> str:
    return (
        "Translate only the section titled 'Assistant Message To Translate' into Chinese. "
        "Use 'Context: User Input' only to understand intent; do not translate or repeat it. "
        "Preserve code, commands, file paths, JSON, URLs, logs, and quoted source text exactly. "
        "If a technical term is clearer in English, keep it and add a short Chinese explanation "
        "only if needed. Return only the translated assistant message in Markdown."
    )


def ensure_command_exists(cmd: list[str], env: dict[str, str] | None = None) -> None:
    executable = cmd[0]
    command_path = (env or command_env()).get("PATH")
    if shutil.which(executable, path=command_path) is None:
        raise SystemExit(f"required command not found on PATH: {executable}")


def command_env() -> dict[str, str]:
    env = os.environ.copy()
    path_parts = [part for part in env.get("PATH", "").split(os.pathsep) if part]
    seen = set(path_parts)
    for raw_path in COMMON_PATH_DIRS:
        path = str(Path(raw_path).expanduser())
        if path not in seen:
            path_parts.append(path)
            seen.add(path)
    env["PATH"] = os.pathsep.join(path_parts)
    return env


def auth_hint(agent: str, stderr: str) -> str | None:
    if agent != "claude":
        return None
    if "Not logged in" not in stderr and "Please run /login" not in stderr:
        return None
    return (
        "srh: Claude Code reported no usable auth in this non-interactive shell. "
        "Verify with `claude auth status`. For Claude Code headless `-p`, run "
        "`claude setup-token` or configure `ANTHROPIC_API_KEY`; otherwise use "
        "`srh --agent opencode`, `srh --agent omp`, `srh --agent agy`, or "
        "`srh --agent codex`."
    )


def copy_pipe(pipe: Any, sink: Any, chunks: list[str]) -> None:
    try:
        while True:
            chunk = pipe.read(1)
            if not chunk:
                break
            chunks.append(chunk)
            sink.write(chunk)
            sink.flush()
    finally:
        pipe.close()


def run_streaming(
    cmd: list[str],
    *,
    stdin_text: str | None,
    cwd: str,
    env: dict[str, str],
    timeout: int,
) -> tuple[int, str, str]:
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE if stdin_text is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=cwd,
        env=env,
    )

    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []
    threads = [
        threading.Thread(
            target=copy_pipe,
            args=(proc.stdout, sys.stdout, stdout_chunks),
            daemon=True,
        ),
        threading.Thread(
            target=copy_pipe,
            args=(proc.stderr, sys.stderr, stderr_chunks),
            daemon=True,
        ),
    ]
    for thread in threads:
        thread.start()

    try:
        if proc.stdin is not None:
            proc.stdin.write(stdin_text or "")
            proc.stdin.close()
        returncode = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        returncode = proc.wait()
        raise SystemExit(f"srh: external agent timed out after {timeout}s: {cmd[0]}")
    finally:
        for thread in threads:
            thread.join(timeout=2)

    return returncode, "".join(stdout_chunks), "".join(stderr_chunks)


def cmd_capture_hook(_args: argparse.Namespace) -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        payload = {"raw": raw, "parse_error": True}
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if should_handle_prompt(payload):
        return run_prompt_command(payload)
    return 0


def should_handle_prompt(payload: dict[str, Any]) -> bool:
    if payload.get("hook_event_name") != "UserPromptSubmit":
        return False
    prompt = str(payload.get("prompt") or "").strip()
    if not prompt:
        return False
    return is_hook_command_text(prompt)


def parse_prompt_command(prompt: str) -> argparse.Namespace:
    try:
        tokens = shlex.split(prompt)
    except ValueError:
        tokens = prompt.split()
    args = tokens[1:] if tokens else []
    parser = argparse.ArgumentParser(prog="srh hook", add_help=False)
    parser.add_argument("--agent", choices=["claude", "opencode", "omp", "agy", "codex"], default=os.environ.get("SRH_AGENT", "claude"))
    parser.add_argument("--model", default=os.environ.get("SRH_MODEL") or None)
    parser.add_argument("--timeout", type=int, default=int(os.environ.get("SRH_TIMEOUT", "180")))
    parser.add_argument("--max-chars", type=int, default=int(os.environ.get("SRH_MAX_CHARS", str(DEFAULT_MAX_CHARS))))
    parser.add_argument("--include-tools", action="store_true")
    parser.add_argument("--include-system", action="store_true")
    try:
        return parser.parse_args(args)
    except SystemExit as exc:
        raise ValueError(
            "invalid srh arguments. Supported options: "
            "--agent, --model, --timeout, --max-chars, --include-tools, --include-system"
        ) from exc


def hook_block(reason: str) -> int:
    sys.stdout.write(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False) + "\n")
    return 0


def run_prompt_command(payload: dict[str, Any]) -> int:
    try:
        options = parse_prompt_command(str(payload.get("prompt") or ""))
    except ValueError as exc:
        return hook_block(f"srh: {exc}")

    transcript_value = payload.get("transcript_path")
    if not transcript_value:
        return hook_block("srh: hook input did not include transcript_path")

    transcript = Path(str(transcript_value)).expanduser()
    prompt = default_prompt(DEFAULT_SCOPE)
    try:
        with tempfile.TemporaryDirectory(prefix="shuorenhua-hook-") as tmp:
            input_path = Path(tmp) / "session.md"
            input_path.write_text(
                export_rollout(
                    transcript,
                    include_tools=options.include_tools,
                    include_system=options.include_system,
                    max_chars=options.max_chars,
                    scope=DEFAULT_SCOPE,
                ),
                encoding="utf-8",
            )
            cmd, stdin_text = command_for_agent(options.agent, options.model, prompt, input_path)
            env = command_env()
            if shutil.which(cmd[0], path=env.get("PATH")) is None:
                return hook_block(f"srh: required command not found on PATH: {cmd[0]}")
            try:
                result = subprocess.run(
                    cmd,
                    input=stdin_text,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    cwd=str(payload.get("cwd") or os.getcwd()),
                    env=env,
                    timeout=options.timeout,
                )
            except subprocess.TimeoutExpired:
                return hook_block(f"srh: external agent timed out after {options.timeout}s: {cmd[0]}")
    except (OSError, SystemExit, ValueError) as exc:
        return hook_block(f"srh: {exc}")

    if result.returncode != 0:
        detail = (result.stdout + "\n" + result.stderr).strip()
        hint = auth_hint(options.agent, detail) or ""
        message = f"srh: {cmd[0]} failed with exit code {result.returncode}"
        if detail:
            message += f"\n\n{detail}"
        if hint:
            message += f"\n\n{hint}"
        return hook_block(message)

    output = result.stdout.strip()
    if not output:
        return hook_block(f"srh: external agent returned empty output; agent={options.agent}")
    return hook_block(output)


def cmd_export(args: argparse.Namespace) -> int:
    transcript = resolve_transcript_path(args)
    output = export_rollout(
        transcript,
        include_tools=args.include_tools,
        include_system=args.include_system,
        max_chars=args.max_chars,
        scope=args.scope,
    )
    if args.out:
        out = Path(args.out).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(output, encoding="utf-8")
    else:
        sys.stdout.write(output)
    return 0


def cmd_ask(args: argparse.Namespace) -> int:
    prompt = args.prompt or default_prompt(args.scope)
    tmpdir: tempfile.TemporaryDirectory[str] | None = None
    try:
        if args.input:
            input_path = Path(args.input).expanduser()
            source_path = input_path
        else:
            transcript = resolve_transcript_path(args)
            source_path = transcript
            tmpdir = tempfile.TemporaryDirectory(prefix="shuorenhua-")
            input_path = Path(tmpdir.name) / "session.md"
            input_path.write_text(
                export_rollout(
                    transcript,
                    include_tools=args.include_tools,
                    include_system=args.include_system,
                    max_chars=args.max_chars,
                    scope=args.scope,
                ),
                encoding="utf-8",
            )

        input_chars = len(input_path.read_text(encoding="utf-8"))
        cmd, stdin_text = command_for_agent(args.agent, args.model, prompt, input_path)
        if args.dry_run:
            sys.stdout.write(
                json.dumps(
                    {
                        "cmd": cmd,
                        "stdin": stdin_text is not None,
                        "input": str(input_path),
                        "source": str(source_path),
                        "input_chars": input_chars,
                        "scope": args.scope,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n"
            )
            return 0

        env = command_env()
        ensure_command_exists(cmd, env)
        eprint(
            f"srh: source={source_path} input={input_path} chars={input_chars} "
            f"agent={args.agent} model={args.model or '-'}",
            quiet=args.quiet,
        )
        eprint(f"srh: running {cmd[0]} (timeout {args.timeout}s)", quiet=args.quiet)
        if args.stream and not (args.out or args.stderr_out):
            returncode, stdout, stderr = run_streaming(
                cmd,
                stdin_text=stdin_text,
                cwd=args.cwd or os.getcwd(),
                env=env,
                timeout=args.timeout,
            )
        else:
            result = subprocess.run(
                cmd,
                input=stdin_text,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=args.cwd or os.getcwd(),
                env=env,
                timeout=args.timeout,
            )
            returncode = result.returncode
            stdout = result.stdout
            stderr = result.stderr

        if returncode != 0:
            if stdout and not args.stream:
                sys.stderr.write(stdout)
                if not stdout.endswith("\n"):
                    sys.stderr.write("\n")
            if stderr and not args.stream:
                sys.stderr.write(stderr)
                if not stderr.endswith("\n"):
                    sys.stderr.write("\n")
            hint = auth_hint(args.agent, stdout + "\n" + stderr)
            if hint:
                sys.stderr.write(hint + "\n")
            raise SystemExit(returncode)
        if not stdout.strip():
            if stderr.strip() and (args.out or args.stderr_out):
                sys.stderr.write(stderr)
                if not stderr.endswith("\n"):
                    sys.stderr.write("\n")
            sys.stderr.write(
                "srh: external agent returned empty output; "
                f"agent={args.agent} input={input_path}\n"
            )
            return 1
        if args.out:
            out = Path(args.out).expanduser()
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(stdout, encoding="utf-8")
            eprint(f"srh: wrote {out} chars={len(stdout)}", quiet=args.quiet)
        elif not args.stream:
            sys.stdout.write(stdout)
        if args.stderr_out and stderr:
            err = Path(args.stderr_out).expanduser()
            err.parent.mkdir(parents=True, exist_ok=True)
            err.write_text(stderr, encoding="utf-8")
        return 0
    finally:
        if tmpdir is not None:
            tmpdir.cleanup()


def cmd_research(_args: argparse.Namespace) -> int:
    facts = {
        "codex_hooks": {
            "UserPromptSubmit_input": ["session_id", "turn_id", "transcript_path", "cwd", "prompt", "model", "permission_mode"],
            "Stop_input": ["session_id", "turn_id", "transcript_path", "cwd", "last_assistant_message", "model", "permission_mode"],
            "UserPromptSubmit_output_can_add_context": True,
            "UserPromptSubmit_output_can_block": True,
            "Stop_output_can_add_context": False,
        },
        "agents": {
            "claude": "claude --safe-mode -p <prompt> --model <model>; stdin supported; normal terminal auth may be unavailable inside Codex shell sandbox",
            "opencode": "opencode run --model provider/model <prompt> --file transcript.md",
            "omp": "omp -p --no-tools --no-session --model <model> @transcript.md <prompt>",
            "agy": "agy --print <prompt and transcript>; editor/chat oriented",
            "codex": "codex exec --model <model>; stdin supported as extra context",
        },
    }
    sys.stdout.write(json.dumps(facts, ensure_ascii=False, indent=2) + "\n")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Bridge Codex session transcripts to external coding agents.")
    sub = parser.add_subparsers(dest="command", required=True)

    capture = sub.add_parser("capture-hook", help="Record hook stdin under PLUGIN_DATA.")
    capture.set_defaults(func=cmd_capture_hook)

    export = sub.add_parser("export", help="Export a Codex rollout JSONL transcript to Markdown.")
    add_transcript_args(export)
    export.add_argument("--out")
    export.set_defaults(func=cmd_export)

    ask = sub.add_parser("ask", help="Ask an external coding agent to translate a transcript.")
    add_transcript_args(ask)
    ask.add_argument("--agent", choices=["claude", "opencode", "omp", "agy", "codex"], required=True)
    ask.add_argument("--model")
    ask.add_argument("--input", help="Existing Markdown transcript file.")
    ask.add_argument("--out")
    ask.add_argument("--stderr-out")
    ask.add_argument("--prompt")
    ask.add_argument("--cwd")
    ask.add_argument("--timeout", type=int, default=180)
    ask.add_argument("--dry-run", action="store_true")
    ask.add_argument("--quiet", action="store_true")
    ask.add_argument("--stream", action="store_true")
    ask.set_defaults(func=cmd_ask)

    research = sub.add_parser("research", help="Print researched integration facts as JSON.")
    research.set_defaults(func=cmd_research)
    return parser


def add_transcript_args(parser: argparse.ArgumentParser) -> None:
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--transcript")
    source.add_argument("--session-id")
    source.add_argument("--last-hook", action="store_true")
    source.add_argument("--latest", action="store_true")
    parser.add_argument("--scope", choices=["last-assistant"], default=DEFAULT_SCOPE)
    parser.add_argument("--include-tools", action="store_true")
    parser.add_argument("--include-system", action="store_true")
    parser.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS)


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
