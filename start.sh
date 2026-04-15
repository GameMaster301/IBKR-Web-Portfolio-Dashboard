#!/usr/bin/env bash
# Start IBKR Portfolio Dashboard
cd "$(dirname "$0")"

echo ""
echo " Starting IBKR Portfolio Dashboard..."
echo ""

docker compose up -d || {
    echo ""
    echo " ERROR: Could not start the dashboard."
    echo " Make sure Docker Desktop is running."
    echo ""
    exit 1
}

sleep 5

if command -v open &>/dev/null; then
    open "http://localhost:8050"
elif command -v xdg-open &>/dev/null; then
    xdg-open "http://localhost:8050"
else
    echo " Open http://localhost:8050 in your browser."
fi

echo " Dashboard is running at http://localhost:8050"
echo " To stop it, run: ./stop.sh"
echo ""
