#!/bin/bash

# Name of the session
SESSION_NAME="yeetzbot"

# Check if the session already exists
tmux has-session -t $SESSION_NAME 2>/dev/null

if [ $? != 0 ]; then
  # Session doesn't exist, create it
  echo "🚀 Starting new bot session..."

  # 1. Create detached session
  tmux new-session -d -s $SESSION_NAME

  # 2. Activate venv inside the session
  # Assumes 'venv' is in the current directory
  tmux send-keys -t $SESSION_NAME "source venv/bin/activate" C-m

  # 3. Run the Python script
  tmux send-keys -t $SESSION_NAME "python listener.py" C-m
fi

# Attach to the session (Show the console)
echo "🔌 Attaching to console..."
tmux attach -t $SESSION_NAME