import unittest
import sqlite3
import os
from datetime import datetime, timedelta
import sys

# Add parent directory to path to import match_manager
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from match_manager import MatchManager

class TestMatchSync(unittest.TestCase):
    def setUp(self):
        self.db_path = 'test_bir.sqlite'
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
            
        self.mm = MatchManager(self.db_path)
        self.init_db()
        
    def tearDown(self):
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def init_db(self):
        conn = self.mm.get_db()
        with open('schema.sql', 'r') as f:
            conn.executescript(f.read())
        
        # Create two teams
        conn.execute("INSERT INTO teams (name, password, status) VALUES ('Team A', 'pass', 'available')")
        conn.execute("INSERT INTO teams (name, password, status) VALUES ('Team B', 'pass', 'available')")
        conn.commit()
        conn.close()

    def test_match_flow(self):
        # 1. Matchmaking
        # Team A tries to find match
        match_id = self.mm.try_create_match(1)
        self.assertIsNotNone(match_id)
        
        # Verify both teams are match_pending
        state_a = self.mm.get_user_state(1)
        state_b = self.mm.get_user_state(2)
        
        self.assertEqual(state_a['state'], 'match_pending')
        self.assertEqual(state_b['state'], 'match_pending')
        self.assertEqual(state_a['match']['id'], match_id)
        self.assertEqual(state_b['match']['id'], match_id)
        
        print("Match created successfully")

        # 2. Time travel to ready check
        # Update scheduled_start to be now (simulating buffer time passed)
        conn = self.mm.get_db()
        conn.execute("UPDATE matches SET scheduled_start = ? WHERE id = ?", (datetime.utcnow(), match_id))
        conn.commit()
        conn.close()
        
        # Trigger update
        self.mm.update_match_progress()
        
        state_a = self.mm.get_user_state(1)
        self.assertEqual(state_a['match']['status'], 'ready_check')
        print("Match moved to ready_check")
        
        # 3. Ready up
        self.mm.set_ready(match_id, 1)
        self.mm.set_ready(match_id, 2)
        
        state_a = self.mm.get_user_state(1)
        self.assertEqual(state_a['match']['status'], 'active')
        self.assertEqual(state_a['state'], 'match_active')
        print("Match moved to active")
        
        # 4. Finish match
        self.mm.set_done(match_id, 1)
        self.mm.set_done(match_id, 2)
        
        state_a = self.mm.get_user_state(1)
        self.assertEqual(state_a['match']['status'], 'finished')
        print("Match finished")
        
        # 5. Submit scores
        # Team A submits 10-5
        res = self.mm.submit_score(match_id, 1, 10, 5)
        self.assertEqual(res['result'], 'waiting_for_opponent')
        
        # Team B submits 5-10 (correct)
        res = self.mm.submit_score(match_id, 2, 5, 10)
        self.assertEqual(res['result'], 'match_completed')
        
        # Verify completion
        conn = self.mm.get_db()
        match = conn.execute("SELECT * FROM matches WHERE id = ?", (match_id,)).fetchone()
        self.assertEqual(match['status'], 'completed')
        self.assertEqual(match['winner_id'], 1)
        
        # Verify teams released
        team_a = conn.execute("SELECT * FROM teams WHERE id = 1").fetchone()
        self.assertEqual(team_a['status'], 'no_match')
        self.assertEqual(team_a['wins'], 1)
        
        print("Match completed and stats updated")

if __name__ == '__main__':
    unittest.main()
