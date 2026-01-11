#!/bin/bash
# Samocode uninstall script
# Removes symlinks for skills and commands from ~/.claude/

set -e

SAMOCODE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLAUDE_DIR="$HOME/.claude"

echo "Uninstalling samocode..."
echo ""

# Remove skills symlinks
echo "Removing skills..."
for skill in "$SAMOCODE_DIR/skills/"*/; do
    skill_name=$(basename "$skill")
    target="$CLAUDE_DIR/skills/$skill_name"

    if [ -L "$target" ]; then
        echo "  Removing: $skill_name"
        rm "$target"
    fi
done

# Remove commands symlinks
echo ""
echo "Removing commands..."
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
