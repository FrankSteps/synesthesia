#!/bin/bash

# Run from the project root, regardless of the caller's working directory.
cd "$(dirname "$0")" || exit 1

# Stop any process currently using port 8080.
sudo fuser -k 8080/tcp

# Activate the project's Python virtual environment.
source .venv/bin/activate

# Start the Synesthesia server.
python3 src/synesthesia.py