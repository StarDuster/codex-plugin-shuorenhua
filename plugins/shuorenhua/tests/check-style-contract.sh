#!/bin/sh
set -eu

plugin_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)

PLUGIN_ROOT=$plugin_root node <<'NODE'
const fs = require("fs");
const path = require("path");

const root = process.env.PLUGIN_ROOT;
const read = (relative) => fs.readFileSync(path.join(root, relative), "utf8");

const files = {
  manifest: read(".codex-plugin/plugin.json"),
  hooks: read("hooks/hooks.json"),
  script: read("scripts/session_bridge.py"),
};

function requireIncludes(label, text, needles) {
  for (const needle of needles) {
    if (!text.includes(needle)) {
      throw new Error(`${label} is missing expected text: ${needle}`);
    }
  }
}

requireIncludes("plugin manifest", files.manifest, [
  "srh",
  "说人话",
  "UserPromptSubmit hook",
]);

if (files.manifest.includes('"skills"')) {
  throw new Error("plugin manifest must not expose skill commands");
}

requireIncludes("hooks", files.hooks, [
  "UserPromptSubmit",
  "capture-hook",
]);

requireIncludes("bridge script", files.script, [
  "HOOK_COMMANDS",
  "capture-hook",
  "export_rollout",
  "command_for_agent",
  "claude",
  "opencode",
  "omp",
  "agy",
]);

console.log("Bridge contract valid");
NODE
