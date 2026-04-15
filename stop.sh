#!/usr/bin/env bash
# Stop IBKR Portfolio Dashboard
cd "$(dirname "$0")"
echo ""
echo " Stopping IBKR Portfolio Dashboard..."
docker compose down
echo " Done."
echo ""
