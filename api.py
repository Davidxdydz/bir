from flask import Blueprint, jsonify, g, request
from utils import login_required, get_match_manager

bp = Blueprint('api', __name__, url_prefix='/api')

@bp.route('/toggle_status', methods=['POST'])
@login_required
def toggle_status():
    """
    Toggle between 'no_match' and 'available'.
    If becoming 'available', try to find a match immediately.
    """
    mm = get_match_manager()
    user_id = g.user['id']
    
    # Check current state
    team_state = mm.get_user_state(user_id)
    current_status = team_state['state']
    
    if current_status in ('match_pending', 'match_active'):
        return jsonify({'status': 'error', 'message': 'Already in a match'})
        
    if current_status == 'available':
        # Cancel searching
        conn = mm.get_db()
        conn.execute("UPDATE teams SET status = 'no_match' WHERE id = ?", (user_id,))
        conn.commit()
        conn.close()
        return jsonify({'status': 'cancelled', 'new_state': 'no_match'})
        
    if current_status == 'no_match':
        # Try to find a match with another available team
        result = mm.try_create_match(user_id)
        
        if result:
            return jsonify({'status': 'match_found', 'match_id': result})
        
        # No match found, set to available
        conn = mm.get_db()
        conn.execute("UPDATE teams SET status = 'available' WHERE id = ?", (user_id,))
        conn.commit()
        conn.close()
        return jsonify({'status': 'searching', 'new_state': 'available'})
    
    return jsonify({'status': 'error', 'message': 'Invalid state'})

@bp.route('/match/<int:match_id>/reset_mismatch', methods=['POST'])
@login_required
def reset_mismatch(match_id):
    mm = get_match_manager()
    # Verify user is in this match
    match = mm.get_match_details(match_id)
    if not match or (g.user['id'] != match['team1_id'] and g.user['id'] != match['team2_id']):
        return jsonify({'error': 'Unauthorized'}), 403
        
    mm.reset_mismatch(match_id)
    return jsonify({'status': 'success'})

@bp.route('/status')
@login_required
def status():
    mm = get_match_manager()
    team_state = mm.get_user_state(g.user['id'])
    state_value = team_state.get('state', 'no_match')

    if state_value in ('match_pending', 'match_active') and team_state.get('match'):
        match = team_state['match']
        is_team1 = g.user['id'] == match['team1_id']

        submission_state = mm.get_submission_snapshot(match['id'])
        team1_submission = submission_state['team1_submission']
        team2_submission = submission_state['team2_submission']
        
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
            'team1_submitted': team1_submission is not None,
            'team2_submitted': team2_submission is not None,
            'awaiting_submission_team_ids': submission_state['awaiting_team_ids'],
            'awaiting_submission_names': submission_state['awaiting_team_names'],
            'mismatch_flag': bool(match['mismatch_flag']),
            'winner_id': match['winner_id']
        })
    
    return jsonify({'status': state_value})

@bp.route('/check_notifications')
@login_required
def check_notifications():
    """Check if user has a match starting in ~3 minutes and hasn't been notified yet"""
    mm = get_match_manager()
    return jsonify(mm.check_notifications(g.user['id']))

@bp.route('/match/<int:match_id>')
@login_required
def get_match_details(match_id):
    mm = get_match_manager()
    details = mm.get_match_details(match_id)
    
    if not details:
        return jsonify({'error': 'Match not found'}), 404
    
    return jsonify(details)

@bp.route('/match/<int:match_id>/ready', methods=['POST'])
@login_required
def match_ready(match_id):
    mm = get_match_manager()
    success = mm.set_ready(match_id, g.user['id'])
    
    if not success:
            return jsonify({'error': 'Match not found'}), 404
            
    return jsonify({'status': 'success'})

@bp.route('/match/<int:match_id>/done', methods=['POST'])
@login_required
def match_done(match_id):
    mm = get_match_manager()
    success = mm.set_done(match_id, g.user['id'])
    
    if not success:
        return jsonify({'error': 'Match not found'}), 404
        
    return jsonify({'status': 'success'})
