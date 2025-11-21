import sqlite3
import os

db_path = os.path.join('instance', 'bir.sqlite')

def migrate():
    if not os.path.exists(db_path):
        print("Database not found.")
        return

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    try:
        cursor.execute("ALTER TABLE teams ADD COLUMN status TEXT DEFAULT 'no_match'")
        print("Added status column to teams table.")
    except sqlite3.OperationalError as e:
        if "duplicate column name" in str(e):
            print("Column status already exists.")
        else:
            raise e

    # Update status based on existing matches
    # Reset all to no_match first
    cursor.execute("UPDATE teams SET status = 'no_match'")
    
    # Find pending matches
    pending_matches = cursor.execute("SELECT team1_id, team2_id FROM matches WHERE status = 'pending'").fetchall()
    for m in pending_matches:
        cursor.execute("UPDATE teams SET status = 'match_pending' WHERE id IN (?, ?)", (m['team1_id'], m['team2_id']))
        
    # Find active matches
    active_matches = cursor.execute("SELECT team1_id, team2_id FROM matches WHERE status = 'active'").fetchall()
    for m in active_matches:
        cursor.execute("UPDATE teams SET status = 'match_active' WHERE id IN (?, ?)", (m['team1_id'], m['team2_id']))

    conn.commit()
    conn.close()
    print("Migration completed.")

if __name__ == '__main__':
    migrate()
