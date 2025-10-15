Plex FUSE Engine 🚀
A high-performance, multi-instance virtual filesystem for Plex Media Server, built with Python and FUSE. This project mounts one or more remote Plex libraries as local, read-only filesystems on a Linux server, optimized for high-core-count CPUs and fast network connections.
Designed as a robust, self-healing background service managed by systemd, it's the definitive tool for integrating Plex with applications that require direct filesystem access, such as IPTV servers, custom media managers, or analysis scripts.
✨ Key Features
 * Multi-Server Architecture: Run and manage multiple, independent Plex mounts from different servers on a single machine using systemd template services. Each instance is completely isolated.
 * Blazing-Fast Scanning: Utilizes a highly parallel, producer-consumer architecture to scan massive libraries as quickly as possible, fully leveraging multi-core CPUs.
 * Resilient by Design:
   * 🛡️ Self-Healing Connections: Automatically detects and re-establishes dropped connections to the Plex server during file playback.
   * 🦾 Robust Fetching Engine: Uses a "wget-style" direct request model that is resilient to network timeouts and firewall issues.
   * ⚡ Instant Recovery: On restart, instantly loads the last known good cache from a persistent backend (SQLite or Redis/DragonflyDB) to ensure the mount is always available.
 * Live Status Dashboard: Each instance can run an optional, lightweight web server to display its current status, uptime, and real-time performance metrics.
 * On-Demand Rescans: Trigger a fresh library scan for any running instance without restarting the service using the systemctl reload command.
📋 Prerequisites
 * A Linux server with systemd (Tested on Debian/Ubuntu and CentOS/Fedora).
 * Python 3.6+ and pip.
 * Root or sudo access for installation.
 * (Optional) Docker for running a Redis-compatible cache like DragonflyDB.
🗂️ Project Files
Your GitHub repository should contain the following files:
 * install.sh: The main installer script.
 * plex_fuse.py: The core Python FUSE script.
 * cache_manager.py: Handles the persistent cache logic for SQLite and Redis.
 * plex_fuse.ini.template: A template for creating new instance configurations.
 * requirements.txt: A list of the required Python libraries.
 * README.md: This file.
🛠️ Installation
The installer script handles all dependencies, creates the Python virtual environment, and sets up the systemd service template.
 * Clone the Repository:
   git clone <your-repo-url>
cd <your-repo-name>

 * Make the Installer Executable:
   chmod +x install.sh

 * Run the Installer:
   sudo ./install.sh

   > Note: The installer will prompt you for the user you wish to run the service as. This user will need the appropriate permissions for the mount point directories.
   > 
⚙️ Configuration: Adding Your First Server
The installer sets up the framework. You now need to create a configuration for each Plex server you want to mount.
 * Navigate to the installation directory (default is /opt/plexfuse):
   cd /opt/plexfuse

 * Create a config file for your first instance. We'll use server1 as an example.
   cp plex_fuse.ini.template plexfuse@server1.ini

 * Edit the new config file:
   nano plexfuse@server1.ini

   * Fill in the [plex] section with your server's baseurl, token, and desired mountpoint.
   * In the [dashboard] section, assign a unique port number for this instance (e.g., 9988 for server1, 9989 for server2, etc.).
 * Create the mount point directory and set permissions (replace pi:pi with the user and group you chose during installation):
   sudo mkdir -p /mnt/plex/server1
sudo chown pi:pi /mnt/plex/server1

🚀 Service Management
All commands use the systemd instance syntax: plexfuse@<instance_name>.
| Action | Command |
|---|---|
| Enable on Boot | sudo systemctl enable plexfuse@server1 |
| Start Now | sudo systemctl start plexfuse@server1 |
| Check Status | sudo systemctl status plexfuse@server1 |
| View Live Logs | journalctl -u plexfuse@server1 -f |
| Trigger Rescan | sudo systemctl reload plexfuse@server1 |
| Stop | sudo systemctl stop plexfuse@server1 |
| Restart | sudo systemctl restart plexfuse@server1 |
🖥️ Usage
 * Access Files: Your Plex library will be available at the mountpoint you specified (e.g., /mnt/plex/server1).
 * View the Dashboard: From the server's terminal, you can view the live status:
   # Use the port you configured for the instance
curl http://localhost:9988

> To add a second server (e.g., server2), simply repeat the configuration steps, ensuring it has a unique mountpoint and dashboard port.
> 
