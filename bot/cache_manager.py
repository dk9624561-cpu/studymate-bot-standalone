import os
import sqlite3
import json
import logging

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "studymate_cache.db")

def _get_connection():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Initialize SQLite tables for caching API responses."""
    try:
        with _get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS api_cache (
                    cache_key TEXT PRIMARY KEY,
                    data_json TEXT NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()
    except Exception as e:
        logger.error(f"Failed to initialize SQLite cache database: {e}")

def get_cache(key: str):
    """Retrieve cached JSON data by key."""
    try:
        with _get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT data_json, updated_at FROM api_cache WHERE cache_key = ?", (key,))
            row = cursor.fetchone()
            if row:
                return json.loads(row["data_json"])
    except Exception as e:
        logger.error(f"Error reading cache for key '{key}': {e}")
    return None

def set_cache(key: str, data):
    """Save or update JSON data in cache."""
    if data is None:
        return
    try:
        with _get_connection() as conn:
            cursor = conn.cursor()
            data_str = json.dumps(data)
            cursor.execute("""
                INSERT INTO api_cache (cache_key, data_json, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(cache_key) DO UPDATE SET
                    data_json = excluded.data_json,
                    updated_at = CURRENT_TIMESTAMP
            """, (key, data_str))
            conn.commit()
    except Exception as e:
        logger.error(f"Error setting cache for key '{key}': {e}")

# Pre-initialize DB on module import
init_db()
