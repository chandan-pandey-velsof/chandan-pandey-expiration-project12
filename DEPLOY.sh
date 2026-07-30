#!/usr/bin/env bash
# =============================================
#  EaseIP PTA Tool — Update Deployment Script
#  Replaces app code and restarts the service.
#
#  Run this ON THE SERVER after uploading the
#  new files:
#    sudo bash DEPLOY.sh
# =============================================

set -e

GREEN='\033[0;32m'
GOLD='\033[0;33m'
RED='\033[0;31m'
NC='\033[0m'

SERVICE_NAME="easeip-pta"
DEPLOY_DIR="$(cd "$(dirname "$0")" && pwd)"

echo ""
echo -e "${GOLD} =============================================${NC}"
echo -e "${GOLD}   EaseIP PTA Tool — Deploy Update${NC}"
echo -e "${GOLD} =============================================${NC}"
echo ""

# ── Find where the service is currently installed ────────────────────────────
INSTALL_DIR=$(systemctl show "$SERVICE_NAME" --property=WorkingDirectory 2>/dev/null | cut -d= -f2)

if [ -z "$INSTALL_DIR" ] || [ ! -d "$INSTALL_DIR" ]; then
    echo -e "${RED} Could not find existing service install dir.${NC}"
    echo " Falling back to same directory as this script: $DEPLOY_DIR"
    INSTALL_DIR="$DEPLOY_DIR"
fi

echo " Service:     $SERVICE_NAME"
echo " Install dir: $INSTALL_DIR"
echo ""

# ── 1. Stop the service ───────────────────────────────────────────────────────
echo " [1/5] Stopping service..."
sudo systemctl stop "$SERVICE_NAME" 2>/dev/null && \
    echo -e " ${GREEN}✓ Service stopped${NC}" || \
    echo " (service was not running)"

# ── 2. Back up current app files ─────────────────────────────────────────────
BACKUP_DIR="$INSTALL_DIR/backup_$(date +%Y%m%d_%H%M%S)"
echo " [2/5] Backing up current files to $BACKUP_DIR ..."
mkdir -p "$BACKUP_DIR"
for f in app.py pta_engine.py; do
    [ -f "$INSTALL_DIR/$f" ] && cp "$INSTALL_DIR/$f" "$BACKUP_DIR/"
done
[ -d "$INSTALL_DIR/templates" ] && cp -r "$INSTALL_DIR/templates" "$BACKUP_DIR/"
echo -e " ${GREEN}✓ Backup created${NC}"

# ── 3. Install system tools for OCR (Tesseract + Poppler) ────────────────────
echo " [3/6] Installing OCR system tools (Tesseract + Poppler)..."
sudo apt-get update -qq
sudo apt-get install -y --quiet \
    tesseract-ocr \
    poppler-utils \
    python3-dev \
    libjpeg-dev \
    libpng-dev

# English language pack for Tesseract (required for TD PDF OCR)
sudo apt-get install -y --quiet tesseract-ocr-eng 2>/dev/null || true

if command -v tesseract &>/dev/null; then
    echo -e " ${GREEN}✓ Tesseract OCR: $(tesseract --version 2>&1 | head -1)${NC}"
else
    echo -e " ${GOLD}⚠ Tesseract not found — OCR on scanned PDFs unavailable${NC}"
fi

if command -v pdfinfo &>/dev/null; then
    echo -e " ${GREEN}✓ Poppler (pdfinfo): installed${NC}"
else
    echo -e " ${GOLD}⚠ Poppler not found — PDF-to-image conversion unavailable${NC}"
fi

# ── 4. Copy new files ─────────────────────────────────────────────────────────
echo " [4/6] Copying new files..."
cp "$DEPLOY_DIR/app.py"        "$INSTALL_DIR/app.py"
cp "$DEPLOY_DIR/pta_engine.py" "$INSTALL_DIR/pta_engine.py"
cp -r "$DEPLOY_DIR/templates/." "$INSTALL_DIR/templates/"
cp -r "$DEPLOY_DIR/static/."    "$INSTALL_DIR/static/"

# Copy config only if it doesn't already exist (preserve user data)
[ ! -f "$INSTALL_DIR/config.json" ] && cp "$DEPLOY_DIR/config.json" "$INSTALL_DIR/"

echo -e " ${GREEN}✓ Files updated${NC}"

# ── 5. Install/update Python packages ────────────────────────────────────────
echo " [5/6] Updating Python packages..."
VENV_PIP="$INSTALL_DIR/.venv/bin/pip"

if [ -f "$VENV_PIP" ]; then
    "$VENV_PIP" install --upgrade pip --quiet
    "$VENV_PIP" install flask requests cryptography pypdf pdfplumber pymupdf pytesseract pdf2image Pillow openpyxl --quiet
    echo -e " ${GREEN}✓ Python packages updated${NC}"
else
    echo -e " ${GOLD}⚠ No venv found at $INSTALL_DIR/.venv — run start.sh to create it${NC}"
fi

# ── 6. Restart service ────────────────────────────────────────────────────────
echo " [6/6] Restarting service..."
sudo systemctl daemon-reload
sudo systemctl restart "$SERVICE_NAME"
sleep 2

if sudo systemctl is-active --quiet "$SERVICE_NAME"; then
    echo -e " ${GREEN}✓ Service is running!${NC}"
else
    echo -e "${RED} ✗ Service failed to start. Check logs:${NC}"
    echo "   sudo journalctl -u $SERVICE_NAME -n 30"
    exit 1
fi

# ── Done ──────────────────────────────────────────────────────────────────────
LOCAL_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
PORT=$(systemctl show "$SERVICE_NAME" --property=ExecStart 2>/dev/null | grep -o '\-\-port=[0-9]*' | cut -d= -f2 || echo "9090")

echo ""
echo -e "${GOLD} =============================================${NC}"
echo -e "${GOLD}   ✓ Deploy complete!${NC}"
echo -e "${GOLD} =============================================${NC}"
echo ""
echo " Access at:"
echo -e "   ${GREEN}https://localhost:${PORT}${NC}"
echo -e "   ${GREEN}https://$LOCAL_IP:${PORT}${NC}"
echo ""
echo " Rollback if needed:"
echo "   cp $BACKUP_DIR/app.py $INSTALL_DIR/"
echo "   cp $BACKUP_DIR/pta_engine.py $INSTALL_DIR/"
echo "   cp -r $BACKUP_DIR/templates $INSTALL_DIR/"
echo "   sudo systemctl restart $SERVICE_NAME"
echo ""
