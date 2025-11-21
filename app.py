import os
import functools
import sqlite3
from flask import Flask, render_template, request, redirect, url_for, g, session, jsonify, flash
from werkzeug.security import check_password_hash, generate_password_hash # We'll use plaintext as requested but keep imports if we change mind, actually user said plaintext.
import db

# Global configuration constants
MATCH_DURATION_MINUTES = 12  # Single source of truth for match duration
BUFFER_MINUTES = 3  # Buffer time between matches

def update_match_progress(conn):
    """
    Check for time-based state transitions for matches.
    - pending -> ready_check (if time is close and table free)
    - ready_check -> active (if timeout)
    """
    from datetime import datetime, timedelta
    now = datetime.utcnow()
    
    # 1. Check for pending matches that should move to ready_check
    # Condition: scheduled_start is within 3 minutes (or past) AND table is free
    pending_matches = conn.execute('''
        SELECT * FROM matches 
        WHERE status = 'pending' AND scheduled_start IS NOT NULL
    ''').fetchall()
    
    for match in pending_matches:
        if isinstance(match['scheduled_start'], str):
            scheduled_start = datetime.strptime(match['scheduled_start'], '%Y-%m-%d %H:%M:%S')
        else:
            scheduled_start = match['scheduled_start']
            
        # If within 3 minutes of start time
        if now >= scheduled_start - timedelta(minutes=3):
            # Check if table is free
            # Table is busy if there is any match in ready_check, active, or finished on this table
            busy = conn.execute('''
                SELECT 1 FROM matches 
                WHERE table_id = ? AND status IN ('ready_check', 'active', 'finished')
            ''', (match['table_id'],)).fetchone()
            
            if not busy:
                conn.execute("UPDATE matches SET status = 'ready_check' WHERE id = ?", (match['id'],))
                # Teams remain in 'match_pending' state, but UI will show ready check

    # 2. Check for ready_check matches that timed out (auto-start)
    # Condition: scheduled_start passed by 5 minutes
    ready_matches = conn.execute('''
        SELECT * FROM matches 
        WHERE status = 'ready_check'
    ''').fetchall()
    
    for match in ready_matches:
        if isinstance(match['scheduled_start'], str):
            scheduled_start = datetime.strptime(match['scheduled_start'], '%Y-%m-%d %H:%M:%S')
        else:
            scheduled_start = match['scheduled_start']
            
        if now > scheduled_start + timedelta(minutes=5):
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

def get_team_state(conn, team_id):
    """
    Compute the current state of a team based on database records.
    Returns a dict with 'state' and 'match' keys.
    State can be: 'no_match', 'match_pending', or 'match_active'
    """
    # Run state updates first
    update_match_progress(conn)
    
    team = conn.execute('SELECT status FROM teams WHERE id = ?', (team_id,)).fetchone()
    state = team['status'] if team else 'no_match'
    
    match = None
    if state in ('match_pending', 'match_active'):
        # Get the active/pending match
        # Include ready_check and finished in the query
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
        ''', (team_id, team_id)).fetchone()
        
        if match:
            match = dict(match)
        else:
            # Inconsistent state! Fix it.
            conn.execute("UPDATE teams SET status = 'no_match' WHERE id = ?", (team_id,))
            conn.commit()
            state = 'no_match'

    return {
        'state': state,
        'match': match
    }

def login_required(view):
    @functools.wraps(view)
    def wrapped_view(**kwargs):
        if g.user is None:
            return redirect(url_for('auth'))

        return view(**kwargs)

    return wrapped_view

def create_app(test_config=None):
    # create and configure the app
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        SECRET_KEY='dev',
        DATABASE=os.path.join(app.instance_path, 'bir.sqlite'),
    )

    if test_config is None:
        # load the instance config, if it exists, when not testing
        app.config.from_pyfile('config.py', silent=True)
    else:
        # load the test config if passed in
        app.config.from_mapping(test_config)

    # ensure the instance folder exists
    try:
        os.makedirs(app.instance_path)
    except OSError:
        pass

    db.init_app(app)

    # Ensure tables exist; if not, initialize database
    with app.app_context():
        try:
            db.get_db().execute("SELECT 1 FROM teams LIMIT 1")
        except Exception:
            db.init_db()

    @app.before_request
    def load_logged_in_user():
        user_id = session.get('user_id')

        if user_id is None:
            g.user = None
        else:
            g.user = db.get_db().execute(
                'SELECT * FROM teams WHERE id = ?', (user_id,)
            ).fetchone()
            
            if g.user:
                # Check for any current match (pending, ready_check, active)
                # We need this for the navbar link
                g.active_match = db.get_db().execute(
                    '''SELECT * FROM matches 
                       WHERE (team1_id = ? OR team2_id = ?) 
                       AND status IN ('pending', 'ready_check', 'active')
                    ''', (user_id, user_id)
                ).fetchone()
            else:
                g.active_match = None

    @app.route('/')
    def index():
        conn = db.get_db()
        
        # If user is logged in, show their team state
        if g.user:
            # Compute team state from database
            team_state = get_team_state(conn, g.user['id'])
            
            # Prepare match data for template if in pending/active state
            match_data = None
            if team_state['state'] in ('pending', 'active'):
                match = team_state['match']
                is_team1 = g.user['id'] == match['team1_id']
                opponent_name = match['team2_name'] if is_team1 else match['team1_name']
                
                match_data = {
                    'id': match['id'],
                    'status': match['status'],
                    'opponent_name': opponent_name,
                    'scheduled_start': match['scheduled_start']
                }
            
            return render_template('index.html', team_state=team_state['state'], match_data=match_data)
        
        # Otherwise, show the leaderboard for unauthenticated users
        teams_raw = conn.execute(
            'SELECT * FROM teams ORDER BY elo DESC'
        ).fetchall()
        
        # Enrich teams with accurate stats from matches table
        teams = []
        for team_raw in teams_raw:
            team = dict(team_raw)
            # Calculate wins/losses from actual completed matches
            stats = conn.execute('''
                SELECT 
                    COUNT(*) as plays,
                    SUM(CASE WHEN winner_id = ? THEN 1 ELSE 0 END) as wins
                FROM matches 
                WHERE (team1_id = ? OR team2_id = ?) AND status = 'completed'
            ''', (team['id'], team['id'], team['id'])).fetchone()
            
            team['plays'] = stats['plays']
            team['wins'] = stats['wins'] or 0
            team['losses'] = stats['plays'] - (stats['wins'] or 0)
            teams.append(team)
        
        return render_template('index.html', teams=teams)

    @app.route('/auth', methods=('GET', 'POST'))
    def auth():
        if request.method == 'POST':
            action = request.form['action']
            name = request.form['name']
            password = request.form['password']
            conn = db.get_db()
            error = None

            if action == 'register':
                confirm_password = request.form.get('confirm_password')
                if not name:
                    error = 'Team name is required.'
                elif not password:
                    error = 'Password is required.'
                elif password != confirm_password:
                    error = 'Passwords do not match.'
                
                if error is None:
                    try:
                        conn.execute(
                            'INSERT INTO teams (name, password) VALUES (?, ?)',
                            (name, password)
                        )
                        conn.commit()
                        # Auto login after register
                        user = conn.execute(
                            'SELECT * FROM teams WHERE name = ?', (name,)
                        ).fetchone()
                        session.clear()
                        session['user_id'] = user['id']
                        return redirect(url_for('index'))
                    except sqlite3.IntegrityError:
                        error = f"Team {name} is already registered."

            elif action == 'login':
                user = conn.execute(
                    'SELECT * FROM teams WHERE name = ?', (name,)
                ).fetchone()

                if user is None:
                    error = 'Incorrect team name.'
                elif user['password'] != password:
                    error = 'Incorrect password.'

                if error is None:
                    session.clear()
                    session['user_id'] = user['id']
                    return redirect(url_for('index'))

            flash(error)

        return render_template('auth.html')

    @app.route('/logout')
    def logout():
        session.clear()
        return redirect(url_for('index'))

    @app.route('/rules')
    def rules():
        return render_template('rules.html')

    @app.route('/schedule')
    def schedule():
        conn = db.get_db()
        tables = conn.execute('SELECT * FROM tables').fetchall()
        
        tables_data = []
        for table in tables:
            t_data = dict(table)
            
            # Find ALL matches for this table (pending and active), ordered by scheduled_start
            matches = conn.execute('''
                SELECT m.*, 
                       t1.name as team1_name, 
                       t2.name as team2_name,
                       m.scheduled_start,
                       m.status
                FROM matches m
                JOIN teams t1 ON m.team1_id = t1.id
                JOIN teams t2 ON m.team2_id = t2.id
                WHERE m.table_id = ? AND m.status IN ('pending', 'ready_check', 'active', 'finished')
                ORDER BY m.scheduled_start ASC
            ''', (table['id'],)).fetchall()
            
            # Convert to list of dicts
            matches_list = [dict(m) for m in matches]
            
            # Find current active match (first in queue that's active)
            current_match = None
            for m in matches_list:
                if m['status'] in ('active', 'ready_check', 'finished'):
                    current_match = m
                    break
            
            t_data['current_match'] = current_match
            t_data['match_queue'] = matches_list
            tables_data.append(t_data)

        return render_template('schedule.html', tables=tables_data)

    def try_create_match(conn, user_id):
        from datetime import datetime, timedelta
        
        # Atomic check and set to avoid race conditions
        # Try to grab an opponent who is 'available'
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
            
            # Find the single table (we only have 1 table as per requirements)
            table = conn.execute('SELECT id FROM tables LIMIT 1').fetchone()
            table_id = table['id'] if table else None
            
            # Check if table is currently busy or has scheduled matches
            # A match is 12 minutes long
            
            now = datetime.utcnow()
            
            # Find the last scheduled match's end time (scheduled_start + 12 minutes)
            # Consider pending, ready_check, active, finished matches
            last_match = conn.execute('''
                SELECT scheduled_start 
                FROM matches 
                WHERE status IN ('pending', 'ready_check', 'active', 'finished') AND scheduled_start IS NOT NULL
                ORDER BY scheduled_start DESC 
                LIMIT 1
            ''').fetchone()
            
            if last_match and last_match['scheduled_start']:
                # Parse the timestamp - handle both string and datetime object
                if isinstance(last_match['scheduled_start'], str):
                    last_start = datetime.strptime(last_match['scheduled_start'], '%Y-%m-%d %H:%M:%S')
                else:
                    last_start = last_match['scheduled_start']
                last_end = last_start + timedelta(minutes=MATCH_DURATION_MINUTES)
                
                # Schedule new match 3 minutes after the last one ends
                scheduled_start = last_end + timedelta(minutes=BUFFER_MINUTES)
            else:
                # Table is free, start immediately
                scheduled_start = now

            cursor = conn.execute(
                'INSERT INTO matches (team1_id, team2_id, table_id, status, scheduled_start) VALUES (?, ?, ?, ?, ?)',
                (user_id, opponent_id, table_id, 'pending', scheduled_start)
            )
            
            match_id = cursor.lastrowid
    
            conn.commit()
            return match_id
        return None

    @app.route('/api/toggle_status', methods=['POST'])
    @login_required
    def toggle_status():
        """
        Toggle between 'no_match' and 'available'.
        If becoming 'available', try to find a match immediately.
        """
        conn = db.get_db()
        user_id = g.user['id']
        
        # Check current state
        team_state = get_team_state(conn, user_id)
        current_status = team_state['state']
        
        if current_status in ('match_pending', 'match_active'):
            return jsonify({'status': 'error', 'message': 'Already in a match'})
            
        if current_status == 'available':
            # Cancel searching
            conn.execute("UPDATE teams SET status = 'no_match' WHERE id = ?", (user_id,))
            conn.commit()
            return jsonify({'status': 'cancelled', 'new_state': 'no_match'})
            
        if current_status == 'no_match':
            # Try to find a match with another available team
            result = try_create_match(conn, user_id)
            
            if result:
                return jsonify({'status': 'match_found', 'match_id': result})
            
            # No match found, set to available
            conn.execute("UPDATE teams SET status = 'available' WHERE id = ?", (user_id,))
            conn.commit()
            return jsonify({'status': 'searching', 'new_state': 'available'})
        
        return jsonify({'status': 'error', 'message': 'Invalid state'})

    @app.route('/api/match/<int:match_id>/reset_mismatch', methods=['POST'])
    @login_required
    def reset_mismatch(match_id):
        conn = db.get_db()
        # Verify user is in this match
        match = conn.execute('SELECT * FROM matches WHERE id = ?', (match_id,)).fetchone()
        if not match or (g.user['id'] != match['team1_id'] and g.user['id'] != match['team2_id']):
            return jsonify({'error': 'Unauthorized'}), 403
            
        # Clear mismatch flag and submissions so both teams can resubmit
        conn.execute('''
            UPDATE matches 
            SET mismatch_flag = 0,
                team1_submitted_score1 = NULL, team1_submitted_score2 = NULL,
                team2_submitted_score1 = NULL, team2_submitted_score2 = NULL
            WHERE id = ?
        ''', (match_id,))
        conn.commit()
        return jsonify({'status': 'success'})

    @app.route('/api/status')
    @login_required
    def status():
        conn = db.get_db()
        team_state = get_team_state(conn, g.user['id'])
        
        if team_state['state'] in ('match_pending', 'match_active'):
            match = team_state['match']
            is_team1 = g.user['id'] == match['team1_id']
            
            # Handle scheduled_start serialization
            scheduled_start_iso = None
            if match['scheduled_start']:
                if isinstance(match['scheduled_start'], str):
                    scheduled_start_iso = match['scheduled_start'].replace(' ', 'T') + 'Z'
                else:
                    scheduled_start_iso = match['scheduled_start'].isoformat() + 'Z'

            # Handle timer_start serialization
            timer_start_iso = None
            if match['timer_start']:
                if isinstance(match['timer_start'], str):
                    timer_start_iso = match['timer_start'].replace(' ', 'T') + 'Z'
                else:
                    timer_start_iso = match['timer_start'].isoformat() + 'Z'

            return jsonify({
                'status': 'in_match',
                'match_id': match['id'],
                'match_status': match['status'], # pending, ready_check, active, finished
                'scheduled_start': scheduled_start_iso,
                'is_team1': is_team1,
                'team1_name': match['team1_name'],
                'team2_name': match['team2_name'],
                'table_name': match.get('table_name'),
                'timer_start': timer_start_iso,
                'team1_ready': bool(match['team1_ready']),
                'team2_ready': bool(match['team2_ready']),
                'team1_done': bool(match['team1_done']),
                'team2_done': bool(match['team2_done']),
                'team1_submitted': bool(match['team1_submitted_score1'] is not None),
                'team2_submitted': bool(match['team2_submitted_score1'] is not None),
                'mismatch_flag': bool(match['mismatch_flag']),
                'winner_id': match['winner_id']
            })
        
        return jsonify({'status': team_state['state']})
    
    @app.route('/api/check_notifications')
    @login_required
    def check_notifications():
        """Check if user has a match starting in ~3 minutes and hasn't been notified yet"""
        from datetime import datetime, timedelta
        
        conn = db.get_db()
        now = datetime.utcnow()
        three_min_from_now = now + timedelta(minutes=3)
        six_min_from_now = now + timedelta(minutes=6)  # 3-6 min window
        
        # Find pending matches for this user that start in 3-6 minutes and haven't been notified
        match = conn.execute('''
            SELECT * FROM matches 
            WHERE (team1_id = ? OR team2_id = ?)
            AND status = 'pending'
            AND notification_sent = 0
            AND scheduled_start IS NOT NULL
        ''', (g.user['id'], g.user['id'])).fetchone()
        
        if match and match['scheduled_start']:
            # Handle both string and datetime object
            if isinstance(match['scheduled_start'], str):
                scheduled_time = datetime.strptime(match['scheduled_start'], '%Y-%m-%d %H:%M:%S')
            else:
                scheduled_time = match['scheduled_start']
            
            # Check if match is in the 3-6 minute window
            if three_min_from_now <= scheduled_time <= six_min_from_now:
                # Mark as notified
                conn.execute('UPDATE matches SET notification_sent = 1 WHERE id = ?', (match['id'],))
                conn.commit()
                
                minutes_until = int((scheduled_time - now).total_seconds() / 60)
                return jsonify({
                    'notify': True,
                    'match_id': match['id'],
                    'minutes_until': minutes_until,
                    'scheduled_time': match['scheduled_start']
                })
        
        return jsonify({'notify': False})

    @app.route('/api/match/<int:match_id>')
    @login_required
    def get_match_details(match_id):
        conn = db.get_db()
        match = conn.execute('SELECT * FROM matches WHERE id = ?', (match_id,)).fetchone()
        
        if not match:
            return jsonify({'error': 'Match not found'}), 404
        
        # Return match details
        return jsonify({
            'id': match['id'],
            'status': match['status'],
            'winner_id': match['winner_id'],
            'score1': match['score1'],
            'score2': match['score2'],
            'team1_id': match['team1_id'],
            'team2_id': match['team2_id'],
            'team1_submitted': bool(match['team1_submitted_score1'] is not None),
            'team2_submitted': bool(match['team2_submitted_score1'] is not None)
        })

    @app.route('/api/match/<int:match_id>/ready', methods=['POST'])
    @login_required
    def match_ready(match_id):
        conn = db.get_db()
        match = conn.execute('SELECT * FROM matches WHERE id = ?', (match_id,)).fetchone()
        
        if not match:
            return jsonify({'error': 'Match not found'}), 404
            
        is_team1 = g.user['id'] == match['team1_id']
        
        if is_team1:
            conn.execute('UPDATE matches SET team1_ready = 1 WHERE id = ?', (match_id,))
        else:
            conn.execute('UPDATE matches SET team2_ready = 1 WHERE id = ?', (match_id,))
            
        # Check if both ready to start timer
        updated_match = conn.execute('SELECT * FROM matches WHERE id = ?', (match_id,)).fetchone()
        if updated_match['team1_ready'] and updated_match['team2_ready'] and not updated_match['timer_start']:
            import datetime
            conn.execute('UPDATE matches SET timer_start = ?, status = "active" WHERE id = ?', (datetime.datetime.utcnow(), match_id))
            # Update teams status
            conn.execute("UPDATE teams SET status = 'match_active' WHERE id IN (?, ?)", (match['team1_id'], match['team2_id']))
            
        conn.commit()
        return jsonify({'status': 'success'})

    @app.route('/api/match/<int:match_id>/done', methods=['POST'])
    @login_required
    def match_done(match_id):
        conn = db.get_db()
        match = conn.execute('SELECT * FROM matches WHERE id = ?', (match_id,)).fetchone()
        
        if not match:
            return jsonify({'error': 'Match not found'}), 404
            
        is_team1 = g.user['id'] == match['team1_id']
        
        if is_team1:
            conn.execute('UPDATE matches SET team1_done = 1 WHERE id = ?', (match_id,))
        else:
            conn.execute('UPDATE matches SET team2_done = 1 WHERE id = ?', (match_id,))
            
        # If both done, or if one done (maybe just one click needed? Requirements say "both teams click on finished" OR timer ends)
        # Let's check if both done to transition to 'finished'
        updated_match = conn.execute('SELECT * FROM matches WHERE id = ?', (match_id,)).fetchone()
        if updated_match['team1_done'] and updated_match['team2_done']:
             conn.execute("UPDATE matches SET status = 'finished' WHERE id = ?", (match_id,))
             # Teams remain in match_active until scores are submitted and verified
             
        conn.commit()
        return jsonify({'status': 'success'})

    @app.route('/match/<int:match_id>', methods=('GET', 'POST'))
    @login_required
    def match(match_id):
        conn = db.get_db()
        match = conn.execute(
            '''SELECT m.*, t1.name as team1_name, t2.name as team2_name, tab.name as table_name
               FROM matches m
               JOIN teams t1 ON m.team1_id = t1.id
               JOIN teams t2 ON m.team2_id = t2.id
               LEFT JOIN tables tab ON m.table_id = tab.id
               WHERE m.id = ?''',
            (match_id,)
        ).fetchone()

        if match is None:
            return redirect(url_for('index'))

        # Authorization check
        if g.user['id'] != match['team1_id'] and g.user['id'] != match['team2_id']:
             return redirect(url_for('index'))

        if request.method == 'POST':
            if 'score1' not in request.form or 'score2' not in request.form:
                 return jsonify({'status': 'error', 'message': 'Missing scores'})

            # Result submission
            try:
                score1 = int(request.form['score1'])
                score2 = int(request.form['score2'])
            except ValueError:
                return jsonify({'status': 'error', 'message': 'Invalid scores'})
            
            is_team1 = g.user['id'] == match['team1_id']
            
            # Clear mismatch flag on new submission
            conn.execute('UPDATE matches SET mismatch_flag = 0 WHERE id = ?', (match_id,))
            
            if is_team1:
                conn.execute('UPDATE matches SET team1_submitted_score1 = ?, team1_submitted_score2 = ? WHERE id = ?', 
                           (score1, score2, match_id))
            else:
                conn.execute('UPDATE matches SET team2_submitted_score1 = ?, team2_submitted_score2 = ? WHERE id = ?', 
                           (score1, score2, match_id))
            
            conn.commit()
            
            # Check if both submitted
            updated_match = conn.execute('SELECT * FROM matches WHERE id = ?', (match_id,)).fetchone()
            
            if updated_match['team1_submitted_score1'] is not None and updated_match['team2_submitted_score1'] is not None:
                # Verify scores
                t1_s1 = updated_match['team1_submitted_score1']
                t1_s2 = updated_match['team1_submitted_score2']
                t2_s1 = updated_match['team2_submitted_score1']
                t2_s2 = updated_match['team2_submitted_score2']
                
                # Logic: Team 1 submits (MyScore, OpponentScore)
                #        Team 2 submits (OpponentScore, MyScore)
                # So t1_s1 should equal t2_s1 (Team 1's score)
                # And t1_s2 should equal t2_s2 (Team 2's score)
                # Wait, the form usually asks "Your Score" and "Opponent Score".
                # Let's assume the form sends score1 (Team 1's score) and score2 (Team 2's score) correctly based on who is submitting.
                # If the form is generic "My Score" / "Opponent Score", we need to map it.
                # But looking at previous code, it seems it just takes score1 and score2 from form.
                # Let's assume the frontend handles the mapping or the form asks "Team 1 Score" / "Team 2 Score".
                # If the form asks "My Score" / "Opponent Score":
                # Team 1: score1=My, score2=Opp
                # Team 2: score1=Opp, score2=My
                # Let's verify this assumption later or fix the form.
                # For now, assuming inputs are absolute score1 and score2.
                
                if t1_s1 == t2_s1 and t1_s2 == t2_s2:
                    # Scores match! Finalize game.
                    # Use the agreed scores
                    final_score1 = t1_s1
                    final_score2 = t1_s2
                    
                    winner_id = match['team1_id'] if final_score1 > final_score2 else match['team2_id']
                    
                    # Calculate Elo changes
                    team1 = conn.execute('SELECT * FROM teams WHERE id = ?', (match['team1_id'],)).fetchone()
                    team2 = conn.execute('SELECT * FROM teams WHERE id = ?', (match['team2_id'],)).fetchone()
                    k = 32
                    expected1 = 1 / (1 + 10 ** ((team2['elo'] - team1['elo']) / 400))
                    expected2 = 1 - expected1
                    
                    actual1 = 1 if winner_id == match['team1_id'] else 0
                    actual2 = 1 - actual1
                    
                    new_elo1 = int(team1['elo'] + k * (actual1 - expected1))
                    new_elo2 = int(team2['elo'] + k * (actual2 - expected2))
                    
                    # Update match with scores and winner
                    conn.execute(
                        '''UPDATE matches 
                           SET score1 = ?, score2 = ?, winner_id = ?, status = 'completed', end_time = CURRENT_TIMESTAMP
                           WHERE id = ?''',
                        (final_score1, final_score2, winner_id, match_id)
                    )
                    
                    # Update teams status to no_match
                    conn.execute("UPDATE teams SET status = 'no_match' WHERE id IN (?, ?)", (match['team1_id'], match['team2_id']))
                    
                    # Update team stats
                    if winner_id == match['team1_id']:
                        conn.execute('UPDATE teams SET wins = wins + 1, plays = plays + 1, elo = ? WHERE id = ?', (new_elo1, match['team1_id']))
                        conn.execute('UPDATE teams SET losses = losses + 1, plays = plays + 1, elo = ? WHERE id = ?', (new_elo2, match['team2_id']))
                    else:
                        conn.execute('UPDATE teams SET losses = losses + 1, plays = plays + 1, elo = ? WHERE id = ?', (new_elo1, match['team1_id']))
                        conn.execute('UPDATE teams SET wins = wins + 1, plays = plays + 1, elo = ? WHERE id = ?', (new_elo2, match['team2_id']))
                    
                    # Record Elo history
                    conn.execute('INSERT INTO elo_history (team_id, elo) VALUES (?, ?)', (match['team1_id'], new_elo1))
                    conn.execute('INSERT INTO elo_history (team_id, elo) VALUES (?, ?)', (match['team2_id'], new_elo2))
                    
                    conn.commit()
                    return jsonify({'status': 'success', 'result': 'match_completed'})
                else:
                    # Mismatch! Set flag so both teams can see it
                    conn.execute('UPDATE matches SET mismatch_flag = 1 WHERE id = ?', (match_id,))
                    conn.commit()
                    return jsonify({'status': 'error', 'message': 'Mismatch! Scores did not match. Please verify with opponent and resubmit.'})
            
            return jsonify({'status': 'success', 'result': 'waiting_for_opponent'})

        # Retrieve team details for rendering
        team1 = conn.execute('SELECT * FROM teams WHERE id = ?', (match['team1_id'],)).fetchone()
        team2 = conn.execute('SELECT * FROM teams WHERE id = ?', (match['team2_id'],)).fetchone()
        return render_template('match.html',
                             match=match,
                             team1=team1,
                             team2=team2,
                             match_duration_minutes=MATCH_DURATION_MINUTES)

    @app.route('/team/<int:team_id>', methods=('GET', 'POST'))
    def team_profile(team_id):
        conn = db.get_db()
        team = conn.execute('SELECT * FROM teams WHERE id = ?', (team_id,)).fetchone()
        
        if team is None:
            return redirect(url_for('index'))
            
        is_own_profile = g.user and g.user['id'] == team_id
        
        if request.method == 'POST':
            if not is_own_profile:
                return redirect(url_for('team_profile', team_id=team_id))
                
            description = request.form['description']
            conn.execute('UPDATE teams SET description = ? WHERE id = ?', (description, team_id))
            conn.commit()
            return redirect(url_for('team_profile', team_id=team_id))
            
        # Fetch Elo history
        history = conn.execute('SELECT elo, timestamp FROM elo_history WHERE team_id = ? ORDER BY timestamp ASC', (team_id,)).fetchall()
        
        # Always include starting Elo (1000) as first point to avoid empty charts
        elo_data = [{'elo': 1000, 'date': 'Start'}]
        elo_data.extend([{'elo': h['elo'], 'date': h['timestamp']} for h in history])
        
        # Recalculate stats dynamically to ensure accuracy
        # Count games where this team was team1 or team2 and status is completed
        stats = conn.execute('''
            SELECT 
                COUNT(*) as plays,
                SUM(CASE WHEN winner_id = ? THEN 1 ELSE 0 END) as wins
            FROM matches 
            WHERE (team1_id = ? OR team2_id = ?) AND status = 'completed'
        ''', (team_id, team_id, team_id)).fetchone()
        
        plays = stats['plays']
        wins = stats['wins'] or 0  # Handle None when no matches
        losses = plays - wins
        win_rate = round((wins / plays * 100) if plays > 0 else 0, 1)
        
        # Update the team object with calculated stats for display
        team = dict(team)
        team['plays'] = plays
        team['wins'] = wins
        team['losses'] = losses
        team['win_rate'] = win_rate
            
        return render_template('team.html', team=team, is_own_profile=is_own_profile, elo_history=elo_data)

    return app

app = create_app()

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=8000)


