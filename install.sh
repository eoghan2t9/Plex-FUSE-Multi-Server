#!/bin/bash
#
# Plex FUSE Multi-Server Installer & Systemd Template Setup (V5 - GitHub Edition)
#
# This script installs the Plex FUSE project from the current directory
# and configures it to run as a systemd "template unit", allowing you to manage
# multiple, independent Plex server mounts easily.
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

if [ ! -f "plex_fuse.py" ] || [ ! -f "cache_manager.py" ]; then
    fail "Could not find required script files. Please run this installer from the root of the project directory."
fi

command -v python3 >/dev/null 2>&1 || fail "Python 3 is not installed. Please install it first."
command -v pip3 >/dev/null 2>&1 || fail "pip3 is not installed (package python3-pip)."

# --- Installation Logic ---
DEFAULT_INSTALL_DIR="/opt/plexfuse"
SERVICE_NAME="plexfuse@" # The '@' is critical for a template unit

read -p "Enter the user to run the services as (e.g., 'pi' or your username): " RUN_USER
if ! id "$RUN_USER" &>/dev/null; then
    fail "User '$RUN_USER' does not exist."
fi
RUN_GROUP=$(id -gn "$RUN_USER")

read -p "Enter installation directory [${DEFAULT_INSTALL_DIR}]: " INSTALL_DIR
INSTALL_DIR=${INSTALL_DIR:-$DEFAULT_INSTALL_DIR}

info "Installing project files to ${INSTALL_DIR}..."
mkdir -p "${INSTALL_DIR}"
# Copy all project files to the installation directory
cp ./* "${INSTALL_DIR}/"
success "Project files copied."


info "Installing system build dependencies..."
if [ -f /etc/debian_version ]; then
    apt-get update && apt-get install -y python3-venv libfuse-dev libsystemd-dev pkg-config
elif [ -f /etc/redhat-release ]; then
    dnf install -y python3-virtualenv fuse-devel systemd-devel pkgconf-pkg-config
else
    warn "Unsupported distro. Please install FUSE and systemd development libraries manually."
fi
success "System build dependencies installed."

info "Setting up Python virtual environment..."
rm -rf "${INSTALL_DIR}/venv"
python3 -m venv "${INSTALL_DIR}/venv"
source "${INSTALL_DIR}/venv/bin/activate"
pip3 install --upgrade pip
pip3 install -r "${INSTALL_DIR}/requirements.txt"
deactivate
success "Python environment created and dependencies installed."

chown -R ${RUN_USER}:${RUN_GROUP} "${INSTALL_DIR}"

info "Creating systemd template service file..."
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
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
# The '%i' is replaced by the instance name (e.g., 'server1')
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
echo -e "To add your first server, follow these steps:"
echo ""
echo -e "  1. ${C_YELLOW}Create a config file from the template:${C_RESET}"
echo -e "     cd ${INSTALL_DIR}"
echo -e "     cp plex_fuse.ini.template plexfuse@${C_GREEN}server1${C_RESET}.ini"
echo ""
echo -e "  2. ${C_YELLOW}Edit the new config file:${C_RESET}"
echo -e "     nano plexfuse@${C_GREEN}server1${C_RESET}.ini"
echo -e "     (Fill in baseurl, token, mountpoint, and a unique dashboard port)"
echo ""
echo -e "  3. ${C_YELLOW}Enable and start the service for that instance:${C_RESET}"
echo -e "     sudo systemctl enable plexfuse@${C_GREEN}server1${C_RESET}"
echo -e "     sudo systemctl start plexfuse@${C_GREEN}server1${C_RESET}"
echo ""
echo -e "You can repeat these steps for 'server2', 'server3', etc."
echo ""
echo -e "Useful commands:"
echo -e "  Check status:   sudo systemctl status plexfuse@${C_GREEN}server1${C_RESET}"
echo -e "  View logs:      journalctl -u plexfuse@${C_GREEN}server1${C_RESET} -f"
echo -e "  Trigger rescan: sudo systemctl reload plexfuse@${C_GREEN}server1${C_RESET}"
echo ""
