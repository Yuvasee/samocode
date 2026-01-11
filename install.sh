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
echo "Installed:"
echo "  - 9 skills"
echo "  - 14 commands"
echo ""
echo "Restart Claude Code to apply changes."
