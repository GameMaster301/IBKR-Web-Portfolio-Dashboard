#!/usr/bin/env bash
# Update IBKR Portfolio Dashboard to the latest version
cd "$(dirname "$0")"
echo ""
echo " Updating IBKR Portfolio Dashboard..."
docker compose pull && docker compose up -d
echo " Done. Dashboard restarted at http://localhost:8050"
echo ""
