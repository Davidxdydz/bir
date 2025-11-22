import functools
import sqlite3
from flask import Blueprint, flash, g, redirect, render_template, request, session, url_for
import db
from utils import login_required

bp = Blueprint('auth', __name__, url_prefix='/auth')

@bp.route('/', methods=('GET', 'POST'))
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

@bp.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))
