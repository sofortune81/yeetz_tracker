#!/bin/bash

# Find the PID of the python process running "listener.py"
# -f searches the full command line for the pattern
PID=$(pgrep -f "python listener.py")

if [ -z "$PID" ]; then
    echo "⚠️  Bot is not currently running."
else
    # Kill the process
    kill $PID
    echo "🛑 Stopped Bot (PID: $PID)"

    # Optional: cleanup the empty tmux session if using the start script above
    tmux kill-session -t yeetzbot 2>/dev/null
fi