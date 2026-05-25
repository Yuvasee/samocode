#!/bin/bash
# Samocode uninstall script
# Removes symlinks for Claude Code and Codex.

set -e

SAMOCODE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLAUDE_DIR="$HOME/.claude"
CODEX_DIR="${CODEX_HOME:-$HOME/.codex}"

echo "Uninstalling samocode..."
echo ""

# Remove Claude skills symlinks
echo "Removing Claude skills..."
for skill in "$SAMOCODE_DIR/skills/"*/; do
    skill_name=$(basename "$skill")
    target="$CLAUDE_DIR/skills/$skill_name"

    if [ -L "$target" ]; then
        echo "  Removing: $skill_name"
        rm "$target"
    fi
done

# Remove Codex skills symlinks
echo ""
echo "Removing Codex skills..."
for skill in "$SAMOCODE_DIR/skills/"*/; do
    skill_name=$(basename "$skill")
    target="$CODEX_DIR/skills/$skill_name"

    if [ -L "$target" ]; then
        echo "  Removing: $skill_name"
        rm "$target"
    fi
done

# Remove Claude agents symlinks
echo ""
echo "Removing Claude agents..."
for agent in "$SAMOCODE_DIR/agents/"*.md; do
    [ -f "$agent" ] || continue  # Skip if no matches
    agent_name=$(basename "$agent")
    target="$CLAUDE_DIR/agents/$agent_name"

    if [ -L "$target" ]; then
        echo "  Removing: $agent_name"
        rm "$target"
    fi
done

# Remove Claude commands symlinks
echo ""
echo "Removing Claude commands..."
for cmd in "$SAMOCODE_DIR/commands/"*.md; do
    cmd_name=$(basename "$cmd")
    target="$CLAUDE_DIR/commands/$cmd_name"

    if [ -L "$target" ]; then
        echo "  Removing: $cmd_name"
        rm "$target"
    fi
done

echo ""
echo "Uninstall complete!"
