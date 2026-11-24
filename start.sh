#!/usr/bin/env bash
# Starts the flight-disruption-agent Streamlit app.
#
# Architecture note: there's no separate "agent" process or "MCP server"
# process to start independently. The coordinator ("the agent") runs
# in-process inside whichever Python process imports app.coordinator, and
# app/mcp_client.py auto-launches mcp_server/server.py as a stdio child
# process the first time a tool is called (see README.md). Starting the
# Streamlit app below brings all three up together.
set -euo pipefail
cd "$(dirname "$0")"

# Reuse an already-set-up venv if one exists; otherwise create one with the
# newest available Python >= 3.10 (the `mcp` package requires >= 3.10).
VENV_DIR=""
for candidate in .venv-py312 .venv-py311 .venv-py310 .venv; do
    if [ -x "$candidate/bin/python" ]; then
        VENV_DIR="$candidate"
        break
    fi
done

if [ -z "$VENV_DIR" ]; then
    PYBIN=""
    for py in python3.13 python3.12 python3.11 python3.10; do
        if command -v "$py" >/dev/null 2>&1; then
            PYBIN="$py"
            break
        fi
    done
    if [ -z "$PYBIN" ]; then
        echo "No Python >= 3.10 found (required by the 'mcp' package). Install one, e.g.:" >&2
        echo "  brew install python@3.12" >&2
        exit 1
    fi
    VENV_DIR=".venv"
    echo "Creating virtual environment ($PYBIN) in $VENV_DIR..."
    "$PYBIN" -m venv "$VENV_DIR"
fi

PIP="$VENV_DIR/bin/pip"
MARKER="$VENV_DIR/.deps-installed"

if [ ! -f "$MARKER" ] || [ requirements.txt -nt "$MARKER" ]; then
    echo "Installing dependencies into $VENV_DIR..."
    "$PIP" install -q -r requirements.txt
    touch "$MARKER"
fi

if [ ! -f .env ]; then
    echo "No .env found. Copying .env.example -- edit it with your real LLM_API_KEY, then re-run." >&2
    cp .env.example .env
    exit 1
fi

echo "Starting Streamlit app -- the coordinator runs in-process here, and the"
echo "MCP tools server auto-launches as a stdio child process on first tool call."
echo

exec "$VENV_DIR/bin/streamlit" run streamlit_app.py "$@"
