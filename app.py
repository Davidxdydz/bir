import os
import sqlite3
from flask import Flask, g, session
import db

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
        conn = db.get_db()
        try:
            conn.execute("SELECT 1 FROM teams LIMIT 1")
        except Exception:
            db.init_db()
            conn = db.get_db()

        # Ensure submission tracking table exists for legacy databases
        conn.execute('''
            CREATE TABLE IF NOT EXISTS match_submissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id INTEGER NOT NULL,
                team_id INTEGER NOT NULL,
                score_for INTEGER NOT NULL,
                score_against INTEGER NOT NULL,
                submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(match_id, team_id),
                FOREIGN KEY (match_id) REFERENCES matches (id) ON DELETE CASCADE,
                FOREIGN KEY (team_id) REFERENCES teams (id) ON DELETE CASCADE
            )
        ''')
        conn.commit()

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

    # Register Blueprints
    from auth import bp as auth_bp
    app.register_blueprint(auth_bp)

    from api import bp as api_bp
    app.register_blueprint(api_bp)

    from views import bp as views_bp
    app.register_blueprint(views_bp)
    app.add_url_rule('/', endpoint='index') # Alias for root

    return app

app = create_app()

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=8000)
