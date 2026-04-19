#!/usr/bin/env bash
# IBKR Portfolio Dashboard — Mac / Linux Installer
# ─────────────────────────────────────────────────
# Run once in Terminal:
#   curl -fsSL https://raw.githubusercontent.com/GameMaster301/IBKR-TWS-Web-Portfolio-Dashboard/main/install.sh | bash

set -e

REPO="GameMaster301/IBKR-TWS-Web-Portfolio-Dashboard"
RAW="https://raw.githubusercontent.com/$REPO/main"
DIR="$HOME/ibkrdash"

echo ""
echo "  ╔══════════════════════════════════════════╗"
echo "  ║     IBKR Portfolio Dashboard             ║"
echo "  ║     Installer                            ║"
echo "  ╚══════════════════════════════════════════╝"
echo ""

# ── Step 1: Check Docker ───────────────────────────────────────────────────
printf "  [1/4] Checking Docker Desktop... "
if ! command -v docker &>/dev/null || ! docker info &>/dev/null 2>&1; then
    echo "NOT FOUND"
    echo ""
    echo "  Docker Desktop is required and takes ~2 minutes to install."
    if [[ "$OSTYPE" == "darwin"* ]]; then
        echo "  Download it from: https://www.docker.com/products/docker-desktop/"
        echo ""
        read -p "  Open the download page now? [Y/n] " open_docker
        if [[ "$open_docker" != "n" && "$open_docker" != "N" ]]; then
            open "https://www.docker.com/products/docker-desktop/"
        fi
    else
        echo "  Install guide: https://docs.docker.com/engine/install/"
    fi
    echo ""
    echo "  After installing Docker Desktop, run this installer again."
    echo ""
    exit 1
fi
echo "OK"

# ── Step 2: Create install folder ─────────────────────────────────────────
printf "  [2/4] Setting up $DIR... "
mkdir -p "$DIR"
echo "OK"

# ── Step 3: Download config files ─────────────────────────────────────────
printf "  [3/4] Downloading files... "
curl -fsSL "$RAW/docker-compose.yml" -o "$DIR/docker-compose.yml"

if [ ! -f "$DIR/.env" ]; then
    cat > "$DIR/.env" <<'EOF'
# ── IBKR connection ────────────────────────────────────────────────────
# Set IBKR_PORT to match your IB Gateway / TWS port:
#   IB Gateway paper:  4002  (default)
#   IB Gateway live:   4001
#   TWS paper:         7497
#   TWS live:          7496
IBKR_HOST=host.docker.internal
IBKR_PORT=4002
IBKR_CLIENT_ID=10
IBKR_READONLY=true

# ── Dashboard ──────────────────────────────────────────────────────────
DASH_PORT=8050
OPEN_BROWSER=0
EOF
fi
echo "OK"

# ── Step 4: Create helper scripts ─────────────────────────────────────────
printf "  [4/4] Creating scripts... "

cat > "$DIR/start.sh" <<'SCRIPT'
#!/usr/bin/env bash
cd "$(dirname "$0")"
echo "Starting IBKR Portfolio Dashboard..."
docker compose up -d || {
    echo ""
    echo " ERROR: Could not start the dashboard."
    echo " Make sure Docker Desktop is running."
    exit 1
}
sleep 4
# Open browser (macOS / Linux)
if command -v open &>/dev/null; then
    open "http://localhost:8050"
elif command -v xdg-open &>/dev/null; then
    xdg-open "http://localhost:8050"
else
    echo " Dashboard is running at http://localhost:8050"
fi
SCRIPT

cat > "$DIR/stop.sh" <<'SCRIPT'
#!/usr/bin/env bash
cd "$(dirname "$0")"
echo "Stopping IBKR Portfolio Dashboard..."
docker compose down
echo "Done."
SCRIPT

cat > "$DIR/update.sh" <<'SCRIPT'
#!/usr/bin/env bash
cd "$(dirname "$0")"
echo "Updating to the latest version..."
docker compose pull && docker compose up -d
echo "Update complete. Dashboard restarted at http://localhost:8050"
SCRIPT

chmod +x "$DIR/start.sh" "$DIR/stop.sh" "$DIR/update.sh"
echo "OK"

# ── macOS: create an app shortcut in Applications ─────────────────────────
if [[ "$OSTYPE" == "darwin"* ]]; then
    APP="$HOME/Desktop/IBKR Dashboard.command"
    echo '#!/usr/bin/env bash' > "$APP"
    echo "\"$DIR/start.sh\"" >> "$APP"
    chmod +x "$APP"
fi

# ── Done ───────────────────────────────────────────────────────────────────
echo ""
echo "  ╔══════════════════════════════════════════╗"
echo "  ║   Installation complete!                 ║"
echo "  ╚══════════════════════════════════════════╝"
echo ""
echo "  HOW TO USE"
echo "  ──────────"
echo "  1. Open IB Gateway (or TWS) and log in"
if [[ "$OSTYPE" == "darwin"* ]]; then
    echo "  2. Double-click  'IBKR Dashboard'  on your Desktop"
else
    echo "  2. Run:  $DIR/start.sh"
fi
echo "  3. Dashboard opens at  http://localhost:8050"
echo ""
echo "  To change the port:  edit $DIR/.env"
echo "  To update later:     run  $DIR/update.sh"
echo ""

read -p "  Launch the dashboard now? [Y/n] " launch
if [[ "$launch" != "n" && "$launch" != "N" ]]; then
    echo ""
    echo "  Pulling image (first run may take a minute)..."
    cd "$DIR"
    docker compose pull
    docker compose up -d
    sleep 5
    if command -v open &>/dev/null; then
        open "http://localhost:8050"
    elif command -v xdg-open &>/dev/null; then
        xdg-open "http://localhost:8050"
    else
        echo "  Open http://localhost:8050 in your browser."
    fi
    echo ""
    echo "  Dashboard is live at http://localhost:8050"
    echo ""
fi
