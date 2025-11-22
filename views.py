from flask import Blueprint, render_template, g, redirect, url_for, request, jsonify
import db
from utils import login_required, get_match_manager, MATCH_DURATION_MINUTES

bp = Blueprint('views', __name__)

@bp.route('/')
def index():
    mm = get_match_manager()
    teams = mm.get_leaderboard()
    
    # If user is logged in, show their team state
    if g.user:
        # Compute team state from database
        team_state = mm.get_user_state(g.user['id'])
        state_value = team_state.get('state', 'no_match')
        
        # Prepare match data for template if in pending/active state
        match_data = None
        if state_value in ('match_pending', 'match_active') and team_state.get('match'):
            match = team_state['match']
            is_team1 = g.user['id'] == match['team1_id']
            opponent_name = match['team2_name'] if is_team1 else match['team1_name']
            
            match_data = {
                'id': match['id'],
                'status': match['status'],
                'opponent_name': opponent_name,
                'scheduled_start': match['scheduled_start']
            }
        
        return render_template('index.html', team_state=state_value, match_data=match_data, teams=teams)
    
    # Otherwise, show the leaderboard for unauthenticated users
    return render_template('index.html', teams=teams)

@bp.route('/rules')
def rules():
    return render_template('rules.html')

@bp.route('/schedule')
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

@bp.route('/team/<int:team_id>', methods=('GET', 'POST'))
def team_profile(team_id):
    conn = db.get_db()
    team = conn.execute('SELECT * FROM teams WHERE id = ?', (team_id,)).fetchone()
    
    if team is None:
        return redirect(url_for('views.index'))
        
    is_own_profile = g.user and g.user['id'] == team_id
    
    if request.method == 'POST':
        if not is_own_profile:
            return redirect(url_for('views.team_profile', team_id=team_id))
            
        description = request.form['description']
        conn.execute('UPDATE teams SET description = ? WHERE id = ?', (description, team_id))
        conn.commit()
        return redirect(url_for('views.team_profile', team_id=team_id))
        
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

@bp.route('/play')
@login_required
def play():
    mm = get_match_manager()
    team_state = mm.get_user_state(g.user['id'])
    state_value = team_state.get('state', 'no_match')
    
    match_data = None
    if state_value in ('match_pending', 'match_active') and team_state.get('match'):
        match = team_state['match']
        if match:
            is_team1 = g.user['id'] == match['team1_id']
            opponent_name = match['team2_name'] if is_team1 else match['team1_name']
            
            match_data = {
                'id': match['id'],
                'status': match['status'],
                'opponent_name': opponent_name,
                'scheduled_start': match['scheduled_start']
            }
    
    return render_template('play.html', team_state=state_value, match_data=match_data)

@bp.route('/match/<int:match_id>', methods=('GET', 'POST'))
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
        if request.method == 'POST':
             return jsonify({'status': 'error', 'message': 'Match not found'}), 404
        return redirect(url_for('views.index'))

    # Authorization check
    if g.user['id'] != match['team1_id'] and g.user['id'] != match['team2_id']:
         if request.method == 'POST':
             return jsonify({'status': 'error', 'message': 'Unauthorized'}), 403
         return redirect(url_for('views.index'))

    if request.method == 'POST':
        if 'score1' not in request.form or 'score2' not in request.form:
             return jsonify({'status': 'error', 'message': 'Missing scores'})

        # Result submission
        try:
            score1 = int(request.form['score1'])
            score2 = int(request.form['score2'])
        except ValueError:
            return jsonify({'status': 'error', 'message': 'Invalid scores'})

        if not (0 <= score1 <= 10) or not (0 <= score2 <= 10):
            return jsonify({'status': 'error', 'message': 'Scores must be between 0 and 10'}), 400
        
        is_team1 = g.user['id'] == match['team1_id']
        if match['status'] == 'completed':
            return jsonify({'status': 'error', 'message': 'Match already completed.'}), 400

        # Normalize submitted scores relative to the submitting team
        if is_team1:
            my_score, opp_score = score1, score2
        else:
            my_score, opp_score = score2, score1

        mm = get_match_manager()
        result = mm.submit_score(match_id, g.user['id'], my_score, opp_score)
        
        if result['status'] == 'error':
            return jsonify(result), 400
            
        # If success, we need to return the current state for the UI
        # The UI expects awaiting_submission_team_ids etc.
        
        submission_state = mm.get_submission_snapshot(match_id)
        
        return jsonify({
            'status': 'success',
            'result': result.get('result', 'waiting_for_opponent'),
            'awaiting_submission_team_ids': submission_state['awaiting_team_ids'],
            'awaiting_submission_names': submission_state['awaiting_team_names']
        })

    # Retrieve team details for rendering
    team1 = conn.execute('SELECT * FROM teams WHERE id = ?', (match['team1_id'],)).fetchone()
    team2 = conn.execute('SELECT * FROM teams WHERE id = ?', (match['team2_id'],)).fetchone()
    return render_template('match.html',
                         match=match,
                         team1=team1,
                         team2=team2,
                         match_duration_minutes=MATCH_DURATION_MINUTES)
