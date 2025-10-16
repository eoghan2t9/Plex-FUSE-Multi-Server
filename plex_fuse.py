#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Plex FUSE Mount Script (V6.3 - The Complete Multi-Instance Edition)

This definitive version includes a web status dashboard, on-demand rescans,
a robust producer-consumer engine, and self-healing connections. It now
also automatically creates the mount point directory if it does not exist.
"""

import os
import sys
import errno
import stat
import logging
import json
import time
import platform
import uuid
import subprocess
import signal
from argparse import ArgumentParser
from configparser import ConfigParser
from threading import Lock, Thread, Event
from http.server import BaseHTTPRequestHandler, HTTPServer
from logging.handlers import RotatingFileHandler
from concurrent.futures import ThreadPoolExecutor
from queue import Queue, Empty

try:
    from fuse import FUSE, FuseOSError, Operations, LoggingMixIn
    from plexapi.server import PlexServer
    import requests
except ImportError:
    print('FATAL: Required libraries not found.')
    sys.exit(1)

try:
    from cache_manager import SQLiteCacheManager, RedisCacheManager
except ImportError:
    print('FATAL: cache_manager.py not found.')
    sys.exit(1)

log = logging.getLogger(__name__)

class PlexFUSE(LoggingMixIn, Operations):
    """The definitive, feature-complete FUSE filesystem for Plex."""
    def __init__(self, cfg):
        self.cfg = cfg
        self.plex = None
        self.cache_manager = cfg['cache_manager']
        self.rwlock = Lock()
        self.path_cache, self.dir_map = {}, {}
        
        self.status = "Initializing"
        self.start_time = time.time()
        self.last_scan_finish_time = None
        self.files_opened = 0
        self.data_transferred = 0
        self.rescan_triggered_event = Event()

        cache_loaded = self._perform_initial_cache_load()
        self.session = self._setup_requests_session()
        self.shutdown_event = Event()

        if cfg['refresh_interval_minutes'] >= 0:
            self.refresh_thread = Thread(target=self._refresh_loop, daemon=True)
            self.refresh_thread.start()
            log.info("Background refresh thread started.")

        if cfg['dashboard_enabled']:
            self.dashboard_thread = Thread(target=self._dashboard_worker, daemon=True)
            self.dashboard_thread.start()

        if not cache_loaded:
            log.warning("No initial cache loaded. Waiting for first scan before mounting...")
            self.first_scan_complete_event = Event()
            self.first_scan_complete_event.wait()
            log.info("First scan complete. Proceeding to mount.")

    def _setup_requests_session(self):
        session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(pool_connections=self.cfg['consumer_threads'], pool_maxsize=self.cfg['consumer_threads'])
        session.mount('http://', adapter); session.mount('https://', adapter)
        client_id = str(uuid.uuid4())
        session.headers.update({
            'Connection': 'close', 'X-Plex-Token': self.cfg['token'],
            'X-Plex-Client-Identifier': client_id, 'X-Plex-Product': 'Plex FUSE (V6)',
            'X-Plex-Version': '6.3.0', 'X-Plex-Device': platform.system(),
            'X-Plex-Platform': 'Python',
        })
        log.info("Robust (non-keep-alive) requests session configured.")
        return session

    def _connect_to_plex(self, session=None, timeout=None):
        _session = session or self.session
        _timeout = timeout or self.cfg['network_timeout']
        try:
            log.info("Attempting to establish main Plex connection...")
            self.plex = PlexServer(self.cfg['baseurl'], self.cfg['token'], session=_session, timeout=_timeout)
            log.info(f"Main Plex connection established to: {self.plex.friendlyName}")
            return self.plex
        except Exception as e:
            log.error(f"Failed to establish main Plex connection: {e}")
            self.plex = None
            return None

    def _perform_initial_cache_load(self):
        if self.cache_manager:
            log.info("Attempting to load fresh cache...")
            cached_data = self.cache_manager.load(server_id=self.cfg['instance_name'])
            if cached_data:
                self.path_cache = cached_data.get('path_cache', {}); self.dir_map = cached_data.get('dir_map', {})
                if self.path_cache: log.info(f"Successfully loaded {len(self.path_cache)} items from FRESH cache."); return True
            log.warning("Fresh cache failed. Attempting to load stale cache as fallback.")
            cached_data = self.cache_manager.load(server_id=self.cfg['instance_name'], load_stale=True)
            if cached_data:
                self.path_cache = cached_data.get('path_cache', {}); self.dir_map = cached_data.get('dir_map', {})
                log.warning(f"Successfully loaded {len(self.path_cache)} items from STALE cache."); return True
        log.error("No cache available (fresh or stale)."); return False

    def _producer_thread_worker(self, libraries_data, task_queue):
        log.info("[Producer] Main producer thread starting.")
        try:
            with requests.Session() as producer_session:
                producer_plex = self._connect_to_plex(session=producer_session, timeout=self.cfg['network_timeout'])
                if not producer_plex:
                    log.error("[Producer] Could not establish a dedicated connection. Aborting."); return
                for lib_data in libraries_data:
                    if self.shutdown_event.is_set(): break
                    log.info(f"[Producer] Starting library '{lib_data['title']}'.")
                    try:
                        library = producer_plex.library.sectionByID(lib_data['key'])
                    except Exception as e:
                        log.error(f"[Producer] Could not fetch library object for '{lib_data['title']}': {e}"); continue
                    chunk_sizes = [500, 200, 100, 50]
                    start_index = 0
                    while not self.shutdown_event.is_set():
                        current_chunk_size = chunk_sizes[0]
                        try:
                            api_path = f"/library/sections/{lib_data['key']}/all"
                            params = {'X-Plex-Container-Start': start_index, 'X-Plex-Container-Size': current_chunk_size}
                            items = producer_plex.fetchItems(api_path, **params)
                            if not items:
                                log.info(f"[Producer] Finished fetching all items for library '{lib_data['title']}'."); break
                            for item in items: task_queue.put(item)
                            start_index += len(items)
                            if len(chunk_sizes) > 1: chunk_sizes = [500, 200, 100, 50]
                        except Exception as e:
                            log.warning(f"[Producer] Request failed for '{lib_data['title']}' with chunk size {current_chunk_size}: {e}")
                            chunk_sizes.pop(0)
                            if not chunk_sizes: log.error(f"[Producer] All chunk sizes failed for library '{lib_data['title']}'. Aborting."); break
                            log.warning(f"[Producer] Retrying with smaller chunk size {chunk_sizes[0]}..."); time.sleep(2)
        except Exception as e:
            log.error(f"[Producer] A critical error occurred: {e}", exc_info=True)
        log.info("[Producer] Main producer thread has finished.")

    def _consumer_worker(self, task_queue, final_cache, final_dir_map, lock):
        while not self.shutdown_event.is_set():
            try:
                item = task_queue.get(timeout=1)
                if item is None: break
                item_cache, item_dir_map = {}, {}
                def add_to_maps(path, details):
                    item_cache[path] = details
                    parent = os.path.dirname(path); basename = os.path.basename(path)
                    if parent not in item_dir_map: item_dir_map[parent] = []
                    item_dir_map[parent].append(basename)
                library_root_path = os.path.join('/', item.section().title)
                if item.type == 'movie':
                    year = item.year or 0
                    filename = f"{item.title} ({year}){os.path.splitext(item.media[0].parts[0].file)[1]}"
                    path = os.path.join(library_root_path, filename)
                    add_to_maps(path, {'type': 'file', 'size': item.media[0].parts[0].size, 'key': item.media[0].parts[0].key})
                elif item.type == 'show':
                    show_path = os.path.join(library_root_path, item.title)
                    add_to_maps(show_path, {'type': 'dir'})
                    for season in item.seasons(show_progress=False):
                        season_num = season.seasonNumber if season.seasonNumber is not None else 0
                        season_path = os.path.join(show_path, f"Season {season_num:02d}")
                        add_to_maps(season_path, {'type': 'dir'})
                        for episode in season.episodes(show_progress=False):
                            if hasattr(episode, 'media') and episode.media:
                                part = episode.media[0].parts[0]
                                ep_s_num = episode.seasonNumber if episode.seasonNumber is not None else 0
                                ep_i_num = episode.index if episode.index is not None else 0
                                title = episode.title or "Unknown"
                                filename = f"S{ep_s_num:02d}E{ep_i_num:02d} - {title}{os.path.splitext(part.file)[1]}"
                                path = os.path.join(season_path, filename)
                                add_to_maps(path, {'type': 'file', 'size': part.size, 'key': part.key})
                with lock:
                    final_cache.update(item_cache)
                    for parent, children in item_dir_map.items():
                        if parent not in final_dir_map: final_dir_map[parent] = []
                        final_dir_map[parent].extend(children)
                task_queue.task_done()
            except Empty: continue
            except Exception as e: log.error(f"Error processing item: {e}", exc_info=True); task_queue.task_done()

    def _build_cache_from_plex(self):
        log.info("Building cache using direct-request producer-consumer model...")
        with self.rwlock: self.status = "Scanning"
        plex_conn = self._connect_to_plex()
        if not plex_conn:
            with self.rwlock: self.status = "Error: Connection Failed"
            return {}, {}
        final_cache, final_dir_map = {}, {'/': []}
        task_queue = Queue(maxsize=self.cfg['consumer_threads'] * 4); lock = Lock()
        libraries_data = [{'key': lib.key, 'title': lib.title} for lib in plex_conn.library.sections() if lib.type in ['movie', 'show']]
        for lib_data in libraries_data:
            final_dir_map['/'].append(lib_data['title'])
            final_cache[os.path.join('/', lib_data['title'])] = {'type': 'dir'}
        with ThreadPoolExecutor(max_workers=self.cfg['consumer_threads'] + 1) as executor:
            producer_future = executor.submit(self._producer_thread_worker, libraries_data, task_queue)
            consumers = [executor.submit(self._consumer_worker, task_queue, final_cache, final_dir_map, lock) for _ in range(self.cfg['consumer_threads'])]
            producer_future.result()
            task_queue.join()
            for _ in range(self.cfg['consumer_threads']): task_queue.put(None)
            for future in consumers: future.result()
        log.info("All scan workers have shut down gracefully."); return final_cache, final_dir_map
        
    def _update_cache(self, save_to_persistent_cache=False):
        log.info("Starting background cache update...")
        new_path_cache, new_dir_map = self._build_cache_from_plex()
        if not new_path_cache or not new_dir_map.get('/'):
            log.warning("Scan resulted in an empty library; preserving old cache.")
            with self.rwlock: self.status = "Error: Scan Failed"
            return
        with self.rwlock:
            old_count, new_count = len(self.path_cache), len(new_path_cache)
            self.path_cache, self.dir_map = new_path_cache, new_dir_map
            self.status = "Idle"; self.last_scan_finish_time = time.time()
            log.info(f"Cache refresh complete. Item count: {old_count} -> {new_count}.")
        if save_to_persistent_cache and self.cache_manager and new_count > 0:
            if self.plex: self.cache_manager.save({'path_cache': self.path_cache, 'dir_map': self.dir_map}, self.plex.machineIdentifier)

    def _refresh_loop(self):
        is_first_scan = True
        while not self.shutdown_event.is_set():
            if is_first_scan:
                self._update_cache(save_to_persistent_cache=True)
                if hasattr(self, 'first_scan_complete_event'):
                    self.first_scan_complete_event.set()
                is_first_scan = False
            else:
                interval = self.cfg['refresh_interval_minutes']
                if interval == 0: self.shutdown_event.wait(); break
                log.info(f"Next content scan in {interval} minutes.")
                rescan_triggered = self.rescan_triggered_event.wait(timeout=interval * 60)
                if self.shutdown_event.is_set(): break
                if rescan_triggered:
                    log.info("On-demand rescan triggered by signal.")
                    self.rescan_triggered_event.clear()
                self._update_cache(save_to_persistent_cache=True)

    def _dashboard_worker(self):
        this = self
        class StatusHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200); self.send_header("Content-type", "text/plain"); self.end_headers()
                with this.rwlock:
                    status, items = this.status, len(this.path_cache)
                    files_opened, data_gb = this.files_opened, this.data_transferred / (1024**3)
                    uptime_str = time.strftime('%H:%M:%S', time.gmtime(time.time() - this.start_time))
                    last_scan = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(this.last_scan_finish_time)) if this.last_scan_finish_time else "Never"
                content = (f"--- Plex FUSE Status ({this.cfg['instance_name']}) ---\n"
                           f"Status:         {status}\nUptime:         {uptime_str}\nCached Items:   {items}\nLast Scan:      {last_scan}\n\n"
                           f"--- Metrics ---\nFiles Opened:   {files_opened}\nData Streamed:  {data_gb:.2f} GB\n")
                self.wfile.write(content.encode('utf-8'))
        try:
            port = this.cfg['dashboard_port']
            server_address = ('localhost', port)
            httpd = HTTPServer(server_address, StatusHandler)
            log.info(f"Starting status dashboard for instance '{this.cfg['instance_name']}' at http://localhost:{port}")
            httpd.serve_forever()
        except Exception as e:
            log.error(f"Could not start status dashboard: {e}")

    def destroy(self, path):
        log.info("Filesystem unmounted. Shutting down...")
        self.shutdown_event.set();
        if self.cache_manager: self.cache_manager.close()

    def open(self, path, flags):
        with self.rwlock:
            if path not in self.path_cache or self.path_cache[path]['type'] != 'file': raise FuseOSError(errno.ENOENT)
            self.files_opened += 1
        if (flags & os.O_RDONLY) != os.O_RDONLY: raise FuseOSError(errno.EACCES)
        return 0

    def read(self, path, size, offset, fh):
        with self.rwlock: item_info = self.path_cache.get(path)
        if not item_info: raise FuseOSError(errno.ENOENT)
        if not self.plex and not self._connect_to_plex():
            log.error("Reconnection failed. Cannot read file."); raise FuseOSError(errno.EIO)
        try:
            stream_url = self.plex.url(item_info['key'], includeToken=False)
            headers = {'Range': f'bytes={offset}-{offset + size - 1}'}
            response = self.session.get(stream_url, headers=headers, stream=True, timeout=20)
            response.raise_for_status(); data = response.content
            with self.rwlock: self.data_transferred += len(data)
            return data
        except Exception as e:
            log.error(f"Read failed for {path}: {e}"); self.plex = None; raise FuseOSError(errno.EIO)

    def getattr(self, path, fh=None):
        with self.rwlock:
            if path == '/': return dict(st_mode=(stat.S_IFDIR | 0o755), st_nlink=2)
            if path in self.path_cache:
                info = self.path_cache[path]
                if info['type'] == 'dir': return dict(st_mode=(stat.S_IFDIR | 0o755), st_nlink=2)
                if info['type'] == 'file': return dict(st_mode=(stat.S_IFREG | 0o644), st_size=info.get('size', 0))
        raise FuseOSError(errno.ENOENT)

    def readdir(self, path, fh):
        with self.rwlock: children = self.dir_map.get(path)
        if children is None: raise FuseOSError(errno.ENOENT)
        return ['.', '..'] + children

def main():
    log.info("--- Starting Plex FUSE V6.3 (Auto-Create Mount Point) ---")
    parser = ArgumentParser(description='Mount a Plex server as a background service.')
    parser.add_argument('--config', required=True, help='Path to the instance-specific configuration file.')
    parser.add_argument('--instance', required=True, help='The name of the instance being run (e.g., server1).')
    args = parser.parse_args()
    
    config = ConfigParser(); config.read(args.config)
    cfg = {
        'instance_name': args.instance,
        'baseurl': config.get('plex', 'baseurl'), 'token': config.get('plex', 'token'), 'mountpoint': config.get('plex', 'mountpoint'),
        'cache_type': config.get('cache', 'type', fallback='sqlite'),
        'ttl_hours': config.getint('cache', 'ttl_hours', fallback=24),
        'sqlite_path': config.get('cache', 'sqlite_path', fallback='plex_fuse_cache.db'),
        'redis_host': config.get('redis', 'host', fallback='127.0.0.1'),
        'redis_port': config.getint('redis', 'port', fallback=6379),
        'dashboard_enabled': config.getboolean('dashboard', 'enabled', fallback=False),
        'dashboard_port': config.getint('dashboard', 'port', fallback=9988),
        'foreground': config.getboolean('background', 'foreground', fallback=True),
        'refresh_interval_minutes': config.getint('background', 'refresh_interval_minutes', fallback=60),
        'verbose': config.getboolean('options', 'verbose', fallback=False),
        'allow_other': config.getboolean('options', 'allow_other', fallback=False),
        'consumer_threads': config.getint('performance', 'consumer_threads', fallback=25),
        'network_timeout': config.getint('performance', 'network_timeout', fallback=60),
    }
    setup_logging(logging.DEBUG if cfg['verbose'] else logging.INFO)

    if not all(cfg[k] for k in ['baseurl', 'token', 'mountpoint']):
        log.error('Missing required config: \'baseurl\', \'token\', \'mountpoint\''); sys.exit(1)
    
    # --- NEW: Automatically create the mount point if it doesn't exist ---
    mountpoint = cfg['mountpoint']
    if not os.path.isdir(mountpoint):
        log.warning(f"Mount point '{mountpoint}' does not exist. Attempting to create it.")
        try:
            os.makedirs(mountpoint, exist_ok=True)
            log.info(f"Successfully created mount point directory: {mountpoint}")
        except OSError as e:
            log.critical(f"Failed to create mount point directory '{mountpoint}': {e}")
            log.critical("Please ensure the parent directory exists and the user running the service has write permissions.")
            sys.exit(1)

    cache_manager = None
    if cfg['cache_type'] == 'sqlite':
        db_path = cfg['sqlite_path'].replace('{instance}', args.instance)
        if not os.path.isabs(db_path): db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), db_path)
        cache_manager = SQLiteCacheManager(db_path=db_path, ttl_hours=cfg['ttl_hours'])
    elif cfg['cache_type'] == 'redis':
        try:
            cache_manager = RedisCacheManager(host=cfg['redis_host'], port=cfg['redis_port'], ttl_hours=cfg['ttl_hours'], instance_name=args.instance)
        except Exception as e: log.critical(f"Failed to initialize Redis cache manager: {e}. Aborting."); sys.exit(1)
    cfg['cache_manager'] = cache_manager

    subprocess.run(['fusermount', '-uz', cfg['mountpoint']], check=False, capture_output=True)
    
    plex_fuse = PlexFUSE(cfg)
    
    def handle_sighup(signum, frame):
        log.warning("SIGHUP signal received. Triggering on-demand rescan.")
        plex_fuse.rescan_triggered_event.set()
    signal.signal(signal.SIGHUP, handle_sighup)
    
    try:
        log.info(f"Mounting instance '{args.instance}' to '{cfg['mountpoint']}'.")
        FUSE(plex_fuse, cfg['mountpoint'], foreground=cfg['foreground'], allow_other=cfg['allow_other'])
    except Exception as e: log.critical(f"Mount failed: {e}")
    finally: log.info('FUSE ended.'); plex_fuse.destroy(None)

def setup_logging(level):
    log_format = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    handler = logging.StreamHandler(sys.stdout)
    if 'JOURNAL_STREAM' in os.environ:
        try:
            from systemd.journal import JournalHandler
            handler = JournalHandler(); log.info('Systemd detected. Logging to journal.')
        except ImportError: log.warning('systemd-python not found. Logging to console.')
    handler.setFormatter(log_format)
    root_logger = logging.getLogger()
    if not root_logger.handlers: root_logger.setLevel(level); root_logger.addHandler(handler)

if __name__ == '__main__':
    main()
