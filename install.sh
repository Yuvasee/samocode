#!/bin/bash
# Samocode installation script
# Creates symlinks for skills and commands in ~/.claude/

set -e

SAMOCODE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLAUDE_DIR="$HOME/.claude"

echo "Installing samocode from: $SAMOCODE_DIR"
echo "Target: $CLAUDE_DIR"
echo ""

# Create skills symlinks
echo "Installing skills..."
for skill in "$SAMOCODE_DIR/skills/"*/; do
    skill_name=$(basename "$skill")
    target="$CLAUDE_DIR/skills/$skill_name"

    if [ -L "$target" ]; then
        echo "  Updating: $skill_name"
        rm "$target"
    elif [ -d "$target" ]; then
        echo "  Warning: $skill_name exists and is not a symlink, skipping"
        continue
    else
        echo "  Installing: $skill_name"
    fi

    ln -s "$skill" "$target"
done

# Create agents symlinks
echo ""
echo "Installing agents..."
mkdir -p "$CLAUDE_DIR/agents"
for agent in "$SAMOCODE_DIR/agents/"*.md; do
    [ -f "$agent" ] || continue  # Skip if no matches
    agent_name=$(basename "$agent")
    target="$CLAUDE_DIR/agents/$agent_name"

    if [ -L "$target" ]; then
        echo "  Updating: $agent_name"
        rm "$target"
    elif [ -f "$target" ]; then
        echo "  Warning: $agent_name exists and is not a symlink, skipping"
        continue
    else
        echo "  Installing: $agent_name"
    fi

    ln -s "$agent" "$target"
done

# Create commands symlinks
echo ""
echo "Installing commands..."
for cmd in "$SAMOCODE_DIR/commands/"*.md; do
    cmd_name=$(basename "$cmd")
    target="$CLAUDE_DIR/commands/$cmd_name"

    if [ -L "$target" ]; then
        echo "  Updating: $cmd_name"
        rm "$target"
    elif [ -f "$target" ]; then
        echo "  Warning: $cmd_name exists and is not a symlink, skipping"
        continue
    else
        echo "  Installing: $cmd_name"
    fi

    ln -s "$cmd" "$target"
done

echo ""
echo "Installation complete!"
echo ""
SKILL_COUNT=$(ls -d "$SAMOCODE_DIR/skills/"*/ 2>/dev/null | wc -l | tr -d ' ')
AGENT_COUNT=$(ls "$SAMOCODE_DIR/agents/"*.md 2>/dev/null | wc -l | tr -d ' ')
CMD_COUNT=$(ls "$SAMOCODE_DIR/commands/"*.md 2>/dev/null | wc -l | tr -d ' ')
echo "Installed:"
echo "  - $SKILL_COUNT skills"
echo "  - $AGENT_COUNT agents"
echo "  - $CMD_COUNT commands"
echo ""
echo "============================================================"
echo "IMPORTANT: Project Setup Required"
echo "============================================================"
echo ""
echo "For each project where you use samocode, create a .samocode file:"
echo ""
echo "  MAIN_REPO=~/your-project/repo"
echo "  WORKTREES=~/your-project/worktrees/"
echo "  SESSIONS=~/your-project/_sessions/"
echo ""
echo "Without this file, samocode will refuse to run."
echo ""
echo "============================================================"
echo ""
echo "Restart Claude Code to apply changes."
