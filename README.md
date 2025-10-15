Plex FUSE: A High-Performance Virtual Filesystem for Plex
This project provides a set of Python scripts and configuration to mount one or more remote Plex Media Server libraries as a local, read-only filesystem on a Linux server. It is heavily optimized for high-performance servers with multi-core CPUs and fast network connections.
The system is designed to run as a set of robust, self-healing background services managed by systemd, with each mount being a completely independent instance.
Key Features
Multi-Server Support: Run and manage multiple, independent Plex mounts from different servers on a single machine using systemd template services.
High-Performance Scanning: Utilizes a highly parallel, producer-consumer architecture to scan massive libraries as quickly as possible, leveraging multi-core CPUs.
Resilient by Design:
Self-Healing Connections: Automatically detects and re-establishes dropped connections to the Plex server during file playback.
Robust Scanning: Uses a "wget-style" direct request model that is resilient to network timeouts and firewall issues.
Instant Recovery: On restart, instantly loads the last known good cache from a persistent backend (SQLite or Redis/DragonflyDB) to ensure the mount is always available.
Live Status Dashboard: Each instance can run an optional, lightweight web server to display its current status, uptime, and performance metrics.
On-Demand Rescans: Trigger a fresh library scan for any running instance without restarting the service using systemctl reload.
Prerequisites
A Linux server with systemd. (Tested on Debian/Ubuntu and CentOS/Fedora).
Python 3.6+ and pip.
Root or sudo access for installation.
(Optional) Docker for running a Redis-compatible cache like DragonflyDB.
File Structure
install.sh: The main installer script.
plex_fuse.py: The core Python FUSE script.
cache_manager.py: Handles the persistent cache logic for SQLite and Redis.
plex_fuse.ini.template: A template for creating new instance configurations.
requirements.txt: A list of the required Python libraries.
README.md: This file.
Installation
