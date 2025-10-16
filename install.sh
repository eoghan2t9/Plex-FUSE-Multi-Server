#!/bin/bash
#
# Plex FUSE Multi-Server Installer & Systemd Template Setup (V6 - On-Disk Cache)
#
# This script installs the definitive version of the Plex FUSE script,
# which includes support for a persistent on-disk file cache.
#

# --- Colors for better output ---
C_RESET='\033[0m'
C_RED='\033[0;31m'
C_GREEN='\033[0;32m'
C_YELLOW='\033[0;33m'
C_BLUE='\033[0;34m'

# --- Utility Functions ---
info() { echo -e "${C_BLUE}[INFO]${C_RESET} $1"; }
success() { echo -e "${C_GREEN}[SUCCESS]${C_RESET} $1"; }
warn() { echo -e "${C_YELLOW}[WARNING]${C_RESET} $1"; }
fail() { echo -e "${C_RED}[ERROR]${C_RESET} $1"; exit 1; }

# --- Main Script ---
info "Starting Plex FUSE Multi-Server Installer..."

# --- Pre-flight Checks ---
if [[ $EUID -ne 0 ]]; then
   fail "This script must be run as root. Please use 'sudo ./install.sh'"
fi
if [ ! -f "plex_fuse.py" ]; then fail "Could not find plex_fuse.py. Please run from the project root."; fi

# --- Installation Logic ---
DEFAULT_INSTALL_DIR="/opt/plexfuse"
DEFAULT_CACHE_DIR="/var/cache/plexfuse"
SERVICE_NAME="plexfuse@"

read -p "Enter the user to run the services as: " RUN_USER
if ! id "$RUN_USER" &>/dev/null; then fail "User '$RUN_USER' does not exist."; fi
RUN_GROUP=$(id -gn "$RUN_USER")

read -p "Enter installation directory [${DEFAULT_INSTALL_DIR}]: " INSTALL_DIR
INSTALL_DIR=${INSTALL_DIR:-$DEFAULT_INSTALL_DIR}

read -p "Enter on-disk cache directory [${DEFAULT_CACHE_DIR}]: " CACHE_DIR
CACHE_DIR=${CACHE_DIR:-$DEFAULT_CACHE_DIR}

info "Installing project files to ${INSTALL_DIR}..."
mkdir -p "${INSTALL_DIR}"
cp ./* "${INSTALL_DIR}/"
success "Project files copied."

info "Creating on-disk cache directory at ${CACHE_DIR}..."
mkdir -p "${CACHE_DIR}"
success "Cache directory created."

info "Installing system dependencies..."
if [ -f /etc/debian_version ]; then
    apt-get update && apt-get install -y python3-venv libfuse-dev libsystemd-dev pkg-config
elif [ -f /etc/redhat-release ]; then
    dnf install -y python3-virtualenv fuse-devel systemd-devel pkgconf-pkg-config
else
    warn "Unsupported distro. Please install FUSE and systemd development libraries manually."
fi
success "System dependencies installed."

info "Setting up Python virtual environment..."
rm -rf "${INSTALL_DIR}/venv"
python3 -m venv "${INSTALL_DIR}/venv"
source "${INSTALL_DIR}/venv/bin/activate"
pip3 install --upgrade pip
pip3 install -r "${INSTALL_DIR}/requirements.txt"
deactivate
success "Python environment created and dependencies installed."

# Set permissions for all directories
chown -R ${RUN_USER}:${RUN_GROUP} "${INSTALL_DIR}"
chown -R ${RUN_USER}:${RUN_GROUP} "${CACHE_DIR}"

info "Creating systemd template service file..."
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
# ... (rest of installer is the same)
PYTHON_EXEC="${INSTALL_DIR}/venv/bin/python3"
SCRIPT_EXEC="${INSTALL_DIR}/plex_fuse.py"
cat > "${SERVICE_FILE}" << EOF
[Unit]
Description=Plex FUSE Mount Service for %i
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=simple
User=${RUN_USER}
Group=${RUN_GROUP}
WorkingDirectory=${INSTALL_DIR}
ExecStart=${PYTHON_EXEC} ${SCRIPT_EXEC} --config plexfuse@%i.ini --instance %i
ExecReload=/bin/kill -HUP \$MAINPID
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
success "Systemd template service file created at ${SERVICE_FILE}"
systemctl daemon-reload

echo ""
info "------------------------------------------------------------"
success "Plex FUSE Multi-Server Installation Complete!"
info "------------------------------------------------------------"
echo -e "The system is now ready to run multiple Plex FUSE instances."
echo -e "Remember to edit your config files in '${INSTALL_DIR}' to set the"
echo -e "'on_disk_cache.path' to your desired location, e.g., '${CACHE_DIR}/{instance}'"
echo ""
echo -e "To add a new server, follow the instructions in the README.md file."
