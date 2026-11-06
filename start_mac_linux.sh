#!/usr/bin/env sh
cd "$(dirname "$0")" || exit 1
printf '\nLumad Lingua - Validation Studio\nOpen http://127.0.0.1:8000\n\n'
python3 server.py
