import sqlite3
from datetime import datetime, timedelta

# Configuration constants
MATCH_DURATION_MINUTES = 12
BUFFER_MINUTES = 3
READY_CHECK_TIMEOUT_MINUTES = 5

class MatchManager:
    def __init__(self, db_path):
        self.db_path = db_path

    def get_db(self):
        conn = sqlite3.connect(self.db_path, detect_types=sqlite3.PARSE_DECLTYPES)
        conn.row_factory = sqlite3.Row
        return conn

    def update_match_progress(self):
        """
        Check for time-based state transitions for matches.
        - pending -> ready_check (if time is close and table free)
        - ready_check -> active (if timeout)
        """
        conn = self.get_db()
        try:
            now = datetime.utcnow()
            
            # 1. Check for pending matches that should move to ready_check
            pending_matches = conn.execute('''
                SELECT * FROM matches 
                WHERE status = 'pending' AND scheduled_start IS NOT NULL
            ''').fetchall()
            
            for match in pending_matches:
                scheduled_start = match['scheduled_start']
                if isinstance(scheduled_start, str):
                    scheduled_start = datetime.strptime(scheduled_start, '%Y-%m-%d %H:%M:%S')
                    
                # If within 3 minutes of start time
                if now >= scheduled_start - timedelta(minutes=3):
                    # Check if table is free
                    busy = conn.execute('''
                        SELECT 1 FROM matches 
                        WHERE table_id = ? AND status IN ('ready_check', 'active', 'finished')
                    ''', (match['table_id'],)).fetchone()
                    
                    if not busy:
                        conn.execute("UPDATE matches SET status = 'ready_check' WHERE id = ?", (match['id'],))

            # 2. Check for ready_check matches that timed out (auto-start)
            ready_matches = conn.execute('''
                SELECT * FROM matches 
                WHERE status = 'ready_check'
            ''').fetchall()
            
            for match in ready_matches:
                scheduled_start = match['scheduled_start']
                if isinstance(scheduled_start, str):
                    scheduled_start = datetime.strptime(scheduled_start, '%Y-%m-%d %H:%M:%S')
                    
                if now > scheduled_start + timedelta(minutes=READY_CHECK_TIMEOUT_MINUTES):
                    # Force start
                    conn.execute('''
                        UPDATE matches 
                        SET status = 'active', timer_start = ? 
                        WHERE id = ?
                    ''', (now, match['id']))
                    
                    # Update teams to match_active
                    conn.execute("UPDATE teams SET status = 'match_active' WHERE id IN (?, ?)", 
                               (match['team1_id'], match['team2_id']))

            conn.commit()
        finally:
            conn.close()

    def get_user_state(self, user_id):
        """
        Compute the current state of a user/team based on database records.
        Returns a dict with 'state' and 'match' keys.
        """
        # Run state updates first to ensure consistency
        self.update_match_progress()
        
        conn = self.get_db()
        try:
            team = conn.execute('SELECT status FROM teams WHERE id = ?', (user_id,)).fetchone()
            state = team['status'] if team else 'no_match'
            
            match_data = None
            if state in ('match_pending', 'match_active'):
                match = conn.execute('''
                    SELECT m.*, 
                           t1.name as team1_name, 
                           t2.name as team2_name,
                           tab.name as table_name
                    FROM matches m
                    JOIN teams t1 ON m.team1_id = t1.id
                    JOIN teams t2 ON m.team2_id = t2.id
                    LEFT JOIN tables tab ON m.table_id = tab.id
                    WHERE (m.team1_id = ? OR m.team2_id = ?) 
                    AND m.status IN ('pending', 'ready_check', 'active', 'finished')
                    ORDER BY m.scheduled_start ASC
                    LIMIT 1
                ''', (user_id, user_id)).fetchone()
                
                if match:
                    match_data = dict(match)
                else:
                    # Inconsistent state! Fix it.
                    conn.execute("UPDATE teams SET status = 'no_match' WHERE id = ?", (user_id,))
                    conn.commit()
                    state = 'no_match'

            return {
                'state': state,
                'match': match_data
            }
        finally:
            conn.close()

    def try_create_match(self, user_id):
        """
        Try to find an opponent and create a match.
        """
        conn = self.get_db()
        try:
            # Atomic check and set to avoid race conditions
            cursor = conn.execute('''
                UPDATE teams 
                SET status = 'match_pending' 
                WHERE id = (
                    SELECT id FROM teams 
                    WHERE status = 'available' AND id != ? 
                    ORDER BY RANDOM() LIMIT 1
                )
                RETURNING id
            ''', (user_id,))
            
            row = cursor.fetchone()
            if row:
                opponent_id = row['id']
                
                # Update my status as well
                conn.execute("UPDATE teams SET status = 'match_pending' WHERE id = ?", (user_id,))
                
                # Find the single table
                table = conn.execute('SELECT id FROM tables LIMIT 1').fetchone()
                table_id = table['id'] if table else None
                
                now = datetime.utcnow()
                
                # Find the last scheduled match's end time
                last_match = conn.execute('''
                    SELECT scheduled_start 
                    FROM matches 
                    WHERE status IN ('pending', 'ready_check', 'active', 'finished') AND scheduled_start IS NOT NULL
                    ORDER BY scheduled_start DESC 
                    LIMIT 1
                ''').fetchone()
                
                if last_match and last_match['scheduled_start']:
                    last_start = last_match['scheduled_start']
                    if isinstance(last_start, str):
                        last_start = datetime.strptime(last_start, '%Y-%m-%d %H:%M:%S')
                    last_end = last_start + timedelta(minutes=MATCH_DURATION_MINUTES)
                    scheduled_start = last_end + timedelta(minutes=BUFFER_MINUTES)
                else:
                    scheduled_start = now

                cursor = conn.execute(
                    'INSERT INTO matches (team1_id, team2_id, table_id, status, scheduled_start) VALUES (?, ?, ?, ?, ?)',
                    (user_id, opponent_id, table_id, 'pending', scheduled_start)
                )
                
                match_id = cursor.lastrowid
                conn.commit()
                return match_id
            
            conn.commit()
            return None
        finally:
            conn.close()

    def set_ready(self, match_id, user_id):
        conn = self.get_db()
        try:
            match = conn.execute('SELECT * FROM matches WHERE id = ?', (match_id,)).fetchone()
            if not match:
                return False
                
            is_team1 = user_id == match['team1_id']
            
            if is_team1:
                conn.execute('UPDATE matches SET team1_ready = 1 WHERE id = ?', (match_id,))
            else:
                conn.execute('UPDATE matches SET team2_ready = 1 WHERE id = ?', (match_id,))
                
            # Check if both ready to start timer
            updated_match = conn.execute('SELECT * FROM matches WHERE id = ?', (match_id,)).fetchone()
            if updated_match['team1_ready'] and updated_match['team2_ready'] and not updated_match['timer_start']:
                conn.execute('UPDATE matches SET timer_start = ?, status = "active" WHERE id = ?', 
                           (datetime.utcnow(), match_id))
                conn.execute("UPDATE teams SET status = 'match_active' WHERE id IN (?, ?)", 
                           (match['team1_id'], match['team2_id']))
                
            conn.commit()
            return True
        finally:
            conn.close()

    def set_done(self, match_id, user_id):
        conn = self.get_db()
        try:
            match = conn.execute('SELECT * FROM matches WHERE id = ?', (match_id,)).fetchone()
            if not match:
                return False
                
            is_team1 = user_id == match['team1_id']
            
            if is_team1:
                conn.execute('UPDATE matches SET team1_done = 1 WHERE id = ?', (match_id,))
            else:
                conn.execute('UPDATE matches SET team2_done = 1 WHERE id = ?', (match_id,))
                
            updated_match = conn.execute('SELECT * FROM matches WHERE id = ?', (match_id,)).fetchone()
            if updated_match['team1_done'] and updated_match['team2_done']:
                 conn.execute("UPDATE matches SET status = 'finished' WHERE id = ?", (match_id,))
                 
            conn.commit()
            return True
        finally:
            conn.close()

    def submit_score(self, match_id, user_id, score_for, score_against):
        conn = self.get_db()
        try:
            match = conn.execute('SELECT * FROM matches WHERE id = ?', (match_id,)).fetchone()
            if not match or match['status'] == 'completed':
                return {'status': 'error', 'message': 'Invalid match state'}

            # Insert submission
            conn.execute('''
                INSERT INTO match_submissions (match_id, team_id, score_for, score_against)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(match_id, team_id) DO UPDATE SET
                    score_for = excluded.score_for,
                    score_against = excluded.score_against,
                    submitted_at = CURRENT_TIMESTAMP
            ''', (match_id, user_id, score_for, score_against))
            
            # Clear mismatch flag
            conn.execute('UPDATE matches SET mismatch_flag = 0 WHERE id = ?', (match_id,))
            
            # Check if both submitted
            submissions = conn.execute(
                'SELECT team_id, score_for, score_against FROM match_submissions WHERE match_id = ?',
                (match_id,)
            ).fetchall()
            
            subs_map = {row['team_id']: row for row in submissions}
            
            if len(subs_map) == 2:
                t1_sub = subs_map.get(match['team1_id'])
                t2_sub = subs_map.get(match['team2_id'])
                
                if (t1_sub['score_for'] == t2_sub['score_against'] and 
                    t1_sub['score_against'] == t2_sub['score_for']):
                    
                    # Scores match! Finalize.
                    final_score1 = t1_sub['score_for']
                    final_score2 = t1_sub['score_against']
                    winner_id = match['team1_id'] if final_score1 > final_score2 else match['team2_id']
                    
                    conn.execute('''
                        UPDATE matches 
                        SET status = 'completed', 
                            score1 = ?, score2 = ?, 
                            winner_id = ?, 
                            end_time = CURRENT_TIMESTAMP 
                        WHERE id = ?
                    ''', (final_score1, final_score2, winner_id, match_id))
                    
                    # Update teams
                    conn.execute("UPDATE teams SET status = 'no_match', plays = plays + 1 WHERE id IN (?, ?)",
                               (match['team1_id'], match['team2_id']))
                    
                    # Update winner stats
                    conn.execute("UPDATE teams SET wins = wins + 1 WHERE id = ?", (winner_id,))
                    # Update loser stats
                    loser_id = match['team2_id'] if winner_id == match['team1_id'] else match['team1_id']
                    conn.execute("UPDATE teams SET losses = losses + 1 WHERE id = ?", (loser_id,))
                    
                    # Calculate Elo
                    team1 = conn.execute('SELECT elo FROM teams WHERE id = ?', (match['team1_id'],)).fetchone()
                    team2 = conn.execute('SELECT elo FROM teams WHERE id = ?', (match['team2_id'],)).fetchone()
                    
                    k = 32
                    elo1 = team1['elo']
                    elo2 = team2['elo']
                    
                    expected1 = 1 / (1 + 10 ** ((elo2 - elo1) / 400))
                    expected2 = 1 - expected1

                    actual1 = 1 if winner_id == match['team1_id'] else 0
                    actual2 = 1 - actual1

                    new_elo1 = int(elo1 + k * (actual1 - expected1))
                    new_elo2 = int(elo2 + k * (actual2 - expected2))

                    # Update Elo in teams table
                    conn.execute('UPDATE teams SET elo = ? WHERE id = ?', (new_elo1, match['team1_id']))
                    conn.execute('UPDATE teams SET elo = ? WHERE id = ?', (new_elo2, match['team2_id']))

                    # Record history
                    conn.execute('INSERT INTO elo_history (team_id, elo) VALUES (?, ?)', (match['team1_id'], new_elo1))
                    conn.execute('INSERT INTO elo_history (team_id, elo) VALUES (?, ?)', (match['team2_id'], new_elo2))
                    
                    conn.commit()
                    return {'status': 'success', 'result': 'match_completed'}
                else:
                    # Mismatch
                    conn.execute('UPDATE matches SET mismatch_flag = 1 WHERE id = ?', (match_id,))
                    conn.commit()
                    return {'status': 'error', 'message': 'Scores do not match'}
            
            conn.commit()
            return {'status': 'success', 'result': 'waiting_for_opponent'}
            
        finally:
            conn.close()

    def reset_mismatch(self, match_id):
        conn = self.get_db()
        try:
            conn.execute('''
                UPDATE matches 
                SET mismatch_flag = 0,
                    team1_submitted_score1 = NULL, team1_submitted_score2 = NULL,
                    team2_submitted_score1 = NULL, team2_submitted_score2 = NULL
                WHERE id = ?
            ''', (match_id,))
            conn.execute('DELETE FROM match_submissions WHERE match_id = ?', (match_id,))
            conn.commit()
        finally:
            conn.close()

    def get_submission_snapshot(self, match_id):
        conn = self.get_db()
        try:
            match = conn.execute('SELECT * FROM matches WHERE id = ?', (match_id,)).fetchone()
            if not match:
                return None
                
            submissions = conn.execute(
                'SELECT team_id, score_for, score_against FROM match_submissions WHERE match_id = ?',
                (match_id,)
            ).fetchall()
            
            subs_map = {row['team_id']: {'score_for': row['score_for'], 'score_against': row['score_against']} 
                       for row in submissions}
            
            team1_sub = subs_map.get(match['team1_id'])
            team2_sub = subs_map.get(match['team2_id'])
            
            awaiting_ids = []
            if not team1_sub: awaiting_ids.append(match['team1_id'])
            if not team2_sub: awaiting_ids.append(match['team2_id'])
            
            awaiting_names = []
            if awaiting_ids:
                placeholders = ','.join('?' * len(awaiting_ids))
                rows = conn.execute(f'SELECT name FROM teams WHERE id IN ({placeholders})', awaiting_ids).fetchall()
                awaiting_names = [row['name'] for row in rows]

            return {
                'team1_submission': team1_sub,
                'team2_submission': team2_sub,
                'awaiting_team_ids': awaiting_ids,
                'awaiting_team_names': awaiting_names
            }
        finally:
            conn.close()

    def get_leaderboard(self):
        conn = self.get_db()
        try:
            teams_raw = conn.execute(
                'SELECT * FROM teams ORDER BY elo DESC'
            ).fetchall()

            teams = []
            for team_raw in teams_raw:
                team = dict(team_raw)
                stats = conn.execute('''
                    SELECT 
                        COUNT(*) as plays,
                        SUM(CASE WHEN winner_id = ? THEN 1 ELSE 0 END) as wins
                    FROM matches 
                    WHERE (team1_id = ? OR team2_id = ?) AND status = 'completed'
                ''', (team['id'], team['id'], team['id'])).fetchone()

                plays = stats['plays']
                wins = stats['wins'] or 0
                losses = plays - wins

                team['plays'] = plays
                team['wins'] = wins
                team['losses'] = losses
                teams.append(team)

            return teams
        finally:
            conn.close()

    def check_notifications(self, user_id):
        conn = self.get_db()
        try:
            now = datetime.utcnow()
            three_min_from_now = now + timedelta(minutes=3)
            six_min_from_now = now + timedelta(minutes=6)
            
            match = conn.execute('''
                SELECT * FROM matches 
                WHERE (team1_id = ? OR team2_id = ?)
                AND status = 'pending'
                AND notification_sent = 0
                AND scheduled_start IS NOT NULL
            ''', (user_id, user_id)).fetchone()
            
            if match and match['scheduled_start']:
                if isinstance(match['scheduled_start'], str):
                    scheduled_time = datetime.strptime(match['scheduled_start'], '%Y-%m-%d %H:%M:%S')
                else:
                    scheduled_time = match['scheduled_start']
                
                if three_min_from_now <= scheduled_time <= six_min_from_now:
                    conn.execute('UPDATE matches SET notification_sent = 1 WHERE id = ?', (match['id'],))
                    conn.commit()
                    
                    minutes_until = int((scheduled_time - now).total_seconds() / 60)
                    return {
                        'notify': True,
                        'match_id': match['id'],
                        'minutes_until': minutes_until,
                        'scheduled_time': match['scheduled_start']
                    }
            
            return {'notify': False}
        finally:
            conn.close()

    def get_match_details(self, match_id):
        conn = self.get_db()
        try:
            match = conn.execute('SELECT * FROM matches WHERE id = ?', (match_id,)).fetchone()
            
            if not match:
                return None
            
            team1_submitted = conn.execute(
                'SELECT 1 FROM match_submissions WHERE match_id = ? AND team_id = ? LIMIT 1',
                (match_id, match['team1_id'])
            ).fetchone()
            team2_submitted = conn.execute(
                'SELECT 1 FROM match_submissions WHERE match_id = ? AND team_id = ? LIMIT 1',
                (match_id, match['team2_id'])
            ).fetchone()
            
            return {
                'id': match['id'],
                'status': match['status'],
                'winner_id': match['winner_id'],
                'score1': match['score1'],
                'score2': match['score2'],
                'team1_id': match['team1_id'],
                'team2_id': match['team2_id'],
                'team1_submitted': team1_submitted is not None,
                'team2_submitted': team2_submitted is not None
            }
        finally:
            conn.close()
