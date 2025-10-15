# -*- coding: utf-8 -*-
import sqlite3
import json
import time
import logging
import abc
import sys
try:
    import redis
except ImportError:
    pass

log = logging.getLogger(__name__)

class BaseCacheManager(abc.ABC):
    def __init__(self, ttl_hours):
        self.ttl_seconds = ttl_hours * 3600
    @abc.abstractmethod
    def save(self, data, server_id): pass
    @abc.abstractmethod
    def load(self, server_id, load_stale=False): pass
    def close(self): pass

class SQLiteCacheManager(BaseCacheManager):
    def __init__(self, db_path, ttl_hours):
        super().__init__(ttl_hours)
        self.db_path = db_path
        self.conn = None
        self._init_db()
    def _init_db(self):
        try:
            self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
            cursor = self.conn.cursor()
            cursor.execute('CREATE TABLE IF NOT EXISTS plex_cache (server_id TEXT PRIMARY KEY, cache_data TEXT NOT NULL, timestamp REAL NOT NULL)')
            self.conn.commit()
            log.info(f"SQLite cache initialized at '{self.db_path}'")
        except sqlite3.Error as e:
            log.error(f"SQLite initialization failed: {e}")
            raise
    def save(self, data, server_id):
        try:
            cursor = self.conn.cursor()
            serialized_data = json.dumps(data)
            current_time = time.time()
            cursor.execute('INSERT OR REPLACE INTO plex_cache (server_id, cache_data, timestamp) VALUES (?, ?, ?)', (server_id, serialized_data, current_time))
            self.conn.commit()
            log.info(f"Cache for server '{server_id}' saved to SQLite.")
        except (sqlite3.Error, TypeError) as e:
            log.error(f"Failed to save cache to SQLite: {e}")
    def load(self, server_id, load_stale=False):
        try:
            cursor = self.conn.cursor()
            cursor.execute('SELECT server_id, cache_data, timestamp FROM plex_cache ORDER BY timestamp DESC')
            row = cursor.fetchone()
            if row:
                found_id, cache_data_str, timestamp = row
                is_stale = (time.time() - timestamp) >= self.ttl_seconds
                if not is_stale:
                    log.info(f"Found valid cache for '{found_id}'.")
                    return json.loads(cache_data_str)
                elif load_stale:
                    log.warning(f"Loading STALE cache for '{found_id}'.")
                    return json.loads(cache_data_str)
                else:
                    log.info(f"Cache for '{found_id}' is stale. Deleting.")
                    self._delete(found_id)
            return None
        except (sqlite3.Error, json.JSONDecodeError) as e:
            log.error(f"Failed to load cache from SQLite: {e}")
            return None
    def _delete(self, server_id):
        try:
            cursor = self.conn.cursor()
            cursor.execute('DELETE FROM plex_cache WHERE server_id = ?', (server_id,))
            self.conn.commit()
        except sqlite3.Error as e:
            log.error(f"Failed to delete stale cache: {e}")
    def close(self):
        if self.conn:
            self.conn.close()
            log.info("SQLite connection closed.")

class RedisCacheManager(BaseCacheManager):
    def __init__(self, host, port, ttl_hours, instance_name):
        super().__init__(ttl_hours)
        self.instance_name = instance_name
        if 'redis' not in sys.modules:
            raise ImportError("Redis library is required but not installed.")
        try:
            self.redis_client = redis.StrictRedis(host=host, port=port, db=0, decode_responses=True)
            self.redis_client.ping()
            log.info(f"Successfully connected to Redis/DragonflyDB at {host}:{port}")
        except redis.exceptions.ConnectionError as e:
            log.error(f"Could not connect to Redis/DragonflyDB: {e}")
            raise
    def _get_key(self, server_id):
        # Use the instance name to ensure each service has a unique key
        return f"plexfuse:{self.instance_name}:cache:{server_id}"
    def save(self, data, server_id):
        try:
            key = self._get_key(server_id)
            serialized_data = json.dumps(data)
            self.redis_client.setex(key, int(self.ttl_seconds), serialized_data)
            log.info(f"Cache for instance '{self.instance_name}' saved to Redis with TTL {self.ttl_seconds}s.")
        except Exception as e:
            log.error(f"Failed to save cache to Redis: {e}")
    def load(self, server_id, load_stale=False):
        try:
            key = self._get_key(server_id)
            cached_data = self.redis_client.get(key)
            if cached_data:
                log.info(f"Found valid cache for instance '{self.instance_name}' in Redis.")
                return json.loads(cached_data)
            else:
                # In Redis, if a key doesn't exist, it's either new or expired.
                # The 'stale' concept doesn't apply in the same way as a file timestamp.
                log.info(f"No cache found for instance '{self.instance_name}' in Redis.")
                return None
        except Exception as e:
            log.error(f"Failed to load cache from Redis: {e}")
            return None
    def close(self):
        if self.redis_client:
            self.redis_client.close()
            log.info("Redis connection closed.")
