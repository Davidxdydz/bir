import functools
from flask import g, redirect, url_for, current_app
from match_manager import MatchManager

MATCH_DURATION_MINUTES = 12
BUFFER_MINUTES = 3

def get_match_manager():
    if 'match_manager' not in g:
        g.match_manager = MatchManager(current_app.config['DATABASE'])
    return g.match_manager

def login_required(view):
    @functools.wraps(view)
    def wrapped_view(**kwargs):
        if g.user is None:
            return redirect(url_for('auth.auth')) # Note: will be in auth blueprint
        return view(**kwargs)
    return wrapped_view
