#!/usr/bin/env bash
# =============================================
#  EaseIP PTA Tool — Ubuntu Start Script
#  Version: Testing PTA v3
# =============================================

set -e
cd "$(dirname "$0")"

GREEN='\033[0;32m'
GOLD='\033[0;33m'
RED='\033[0;31m'
NC='\033[0m'

echo ""
echo -e "${GOLD} =============================================${NC}"
echo -e "${GOLD}   EaseIP · Patent Expiration Tool  (v3)${NC}"
echo -e "${GOLD}   Starting server...${NC}"
echo -e "${GOLD} =============================================${NC}"
echo ""

# ── 1. Check Python 3 ────────────────────────────────────────────────────────
if ! command -v python3 &>/dev/null; then
    echo -e "${RED} ERROR: python3 not found.${NC}"
    echo " Run:  sudo apt update && sudo apt install python3 python3-venv -y"
    exit 1
fi

PYTHON=$(command -v python3)
echo -e "${GREEN} ✓ Python:${NC} $($PYTHON --version)"

# ── 2. Virtual environment ────────────────────────────────────────────────────
VENV_DIR="$(pwd)/.venv"
if [ ! -d "$VENV_DIR" ]; then
    echo " Creating virtual environment..."
    if ! $PYTHON -m venv --help &>/dev/null; then
        sudo apt-get install -y python3-venv python3-full
    fi
    $PYTHON -m venv "$VENV_DIR"
    echo -e " ${GREEN}✓ Virtual environment created${NC}"
fi

PYTHON="$VENV_DIR/bin/python"
PIP="$VENV_DIR/bin/pip"

# ── 3. System dependencies (apt) ──────────────────────────────────────────────
echo ""
echo " Checking system tools..."

install_apt_if_missing() {
    if ! command -v "$1" &>/dev/null && ! dpkg -l "$2" &>/dev/null 2>&1; then
        echo "  Installing $2..."
        sudo apt-get install -y "$2" --quiet
    else
        echo -e "  ${GREEN}✓${NC} $2"
    fi
}

install_apt_if_missing pdfinfo poppler-utils
install_apt_if_missing tesseract tesseract-ocr

# ── 4. Python packages ────────────────────────────────────────────────────────
echo ""
echo " Checking Python packages..."

"$PIP" install --upgrade pip --quiet 2>/dev/null || true

install_if_missing() {
    MODULE=$1; PACKAGE=${2:-$1}
    if ! "$PYTHON" -c "import $MODULE" &>/dev/null; then
        echo "  Installing $PACKAGE..."
        "$PIP" install "$PACKAGE" --quiet
    else
        echo -e "  ${GREEN}✓${NC} $PACKAGE"
    fi
}

install_if_missing flask flask
install_if_missing requests requests
install_if_missing cryptography cryptography
install_if_missing pypdf pypdf
install_if_missing pdfplumber pdfplumber
install_if_missing fitz pymupdf
install_if_missing pytesseract pytesseract
install_if_missing pdf2image pdf2image
install_if_missing PIL Pillow
install_if_missing openpyxl openpyxl

# ── 5. SSL cert (generate if missing) ────────────────────────────────────────
if [ ! -f cert.pem ] || [ ! -f key.pem ]; then
    echo ""
    echo " Generating self-signed SSL certificate..."
    openssl req -x509 -newkey rsa:2048 -keyout key.pem -out cert.pem \
        -days 3650 -nodes -subj "/CN=localhost" 2>/dev/null
    echo -e " ${GREEN}✓ SSL certificate generated${NC}"
fi

# ── 6. Start ──────────────────────────────────────────────────────────────────
LOCAL_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
PORT=${PORT:-9090}

echo ""
echo -e "${GOLD} Access at:${NC}"
echo -e "   Local:   ${GREEN}https://localhost:$PORT${NC}"
echo -e "   Network: ${GREEN}https://$LOCAL_IP:$PORT${NC}"
echo ""
echo " Press Ctrl+C to stop."
echo -e "${GOLD} =============================================${NC}"
echo ""

"$PYTHON" app.py --host=0.0.0.0 --port=$PORT
