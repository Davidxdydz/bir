#!/usr/bin/env python3
"""Utility script to clear ALL data from the Beerpong SQLite database.

It removes all records from ALL tables including `teams`, `matches`, etc.
This completely resets the database state.

Usage:
    python clear_db.py

The script assumes the SQLite file is located at `<project_root>/instance/bir.sqlite`
(the same location used by the Flask app). Adjust `DB_PATH` if your setup differs.
"""
import os
import sqlite3
from pathlib import Path

# Path to the SQLite database (relative to this script's location)
DB_PATH = Path(__file__).parent / "instance" / "bir.sqlite"

if not DB_PATH.is_file():
    raise FileNotFoundError(f"Database file not found at {DB_PATH}")

# Tables to clear
TABLES_TO_CLEAR = [
    "matches",
    "elo_history",
    "teams",
    "tables",
]

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

# Disable foreign key constraints to allow deleting in any order
cur.execute("PRAGMA foreign_keys = OFF;")

for table in TABLES_TO_CLEAR:
    try:
        cur.execute(f"DELETE FROM {table};")
        print(f"Cleared table: {table}")
    except sqlite3.Error as e:
        print(f"Warning: could not clear {table}: {e}")

# Reset auto-increment counters
try:
    cur.execute("DELETE FROM sqlite_sequence;")
    print("Reset auto-increment counters.")
except sqlite3.Error as e:
    print(f"Warning: could not reset sqlite_sequence: {e}")

# Re-insert default table
try:
    cur.execute("INSERT INTO tables (name) VALUES ('Table 1');")
    print("Re-inserted default table: Table 1")
except sqlite3.Error as e:
    print(f"Warning: could not insert default table: {e}")

conn.commit()
# Optional: reclaim space
cur.execute("VACUUM;")
conn.close()
print("Database reset complete (ALL data removed).")
