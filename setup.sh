#!/bin/bash
# Quick setup script for gco-pkm-llm

set -e  # Exit on error

echo "========================================="
echo "gco-pkm-llm Setup"
echo "========================================="

# Check for uv
if ! command -v uv &> /dev/null; then
    echo "❌ uv not found. Please install uv first:"
    echo "   curl -LsSf https://astral.sh/uv/install.sh | sh"
    echo "   Or visit: https://github.com/astral-sh/uv"
    exit 1
fi

UV_VERSION=$(uv --version | cut -d' ' -f2)
echo "✓ Found uv $UV_VERSION"

# Check Python version
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 not found. Please install Python 3.9 or later."
    exit 1
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')
echo "✓ Found Python $PYTHON_VERSION"

# Note: uv handles dependencies automatically via PEP-723 metadata
echo "✓ uv will manage dependencies automatically (PEP-723)"

# Check for .env file
if [ ! -f ".env" ]; then
    echo ""
    echo "⚠️  No .env file found"
    echo "Copying .env.example to .env..."
    cp .env.example .env
    echo ""
    echo "📝 IMPORTANT: Edit .env file with your settings:"
    echo "   1. Add your ANTHROPIC_API_KEY"
    echo "   2. Set ORG_DIR to your org-agenda directory"
    echo ""
    echo "Then run: ./setup.sh again to verify"
    exit 0
fi

# Verify .env configuration
source .env

if [ -z "$ANTHROPIC_API_KEY" ] || [ "$ANTHROPIC_API_KEY" = "sk-ant-your-api-key-here" ]; then
    echo "❌ ANTHROPIC_API_KEY not set in .env"
    echo "   Get your key from: https://console.anthropic.com/"
    exit 1
fi

if [ -z "$ORG_DIR" ]; then
    echo "❌ ORG_DIR not set in .env"
    exit 1
fi

if [ ! -d "$ORG_DIR" ]; then
    echo "❌ ORG_DIR does not exist: $ORG_DIR"
    exit 1
fi

echo "✓ ANTHROPIC_API_KEY configured"
echo "✓ ORG_DIR found: $ORG_DIR"

# Check LOGSEQ_DIR (optional)
if [ -n "$LOGSEQ_DIR" ]; then
    if [ -d "$LOGSEQ_DIR" ]; then
        echo "✓ LOGSEQ_DIR found: $LOGSEQ_DIR"
    else
        echo "⚠️  LOGSEQ_DIR set but not found: $LOGSEQ_DIR"
        echo "   (This is optional - server will work without it)"
    fi
else
    echo "ℹ️  LOGSEQ_DIR not set (optional)"
fi

# Check for ripgrep
if ! command -v rg &> /dev/null; then
    echo "⚠️  ripgrep (rg) not found"
    echo "   Install with: brew install ripgrep (macOS)"
    echo "   Or: https://github.com/BurntSushi/ripgrep#installation"
else
    echo "✓ ripgrep found"
fi

# Check for fd
if ! command -v fd &> /dev/null; then
    echo "⚠️  fd not found"
    echo "   Install with: brew install fd (macOS)"
    echo "   Or: https://github.com/sharkdp/fd#installation"
else
    echo "✓ fd found"
fi

# Check for emacs
if ! command -v emacs &> /dev/null; then
    echo "⚠️  emacs not found"
    echo "   Some features (org-ql queries) will not work"
else
    echo "✓ emacs found"
fi

echo ""
echo "========================================="
echo "✅ Setup complete!"
echo "========================================="
echo ""
echo "To start the server:"
echo "  ./pkm-bridge-server.py"
echo ""
echo "Or:"
echo "  uv run pkm-bridge-server.py"
echo ""
echo "Then open: http://localhost:8000"
echo ""
