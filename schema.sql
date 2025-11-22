DROP TABLE IF EXISTS match_submissions;
DROP TABLE IF EXISTS teams;
DROP TABLE IF EXISTS matches;
DROP TABLE IF EXISTS tables;
CREATE TABLE teams (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    password TEXT NOT NULL,
    elo INTEGER DEFAULT 1200,
    wins INTEGER DEFAULT 0,
    losses INTEGER DEFAULT 0,
    plays INTEGER DEFAULT 0,
    is_available BOOLEAN DEFAULT 0,
    description TEXT,
    status TEXT DEFAULT 'no_match'
);
CREATE TABLE tables (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL
);
CREATE TABLE matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    team1_id INTEGER NOT NULL,
    team2_id INTEGER NOT NULL,
    table_id INTEGER,
    start_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    end_time TIMESTAMP,
    score1 INTEGER,
    score2 INTEGER,
    status TEXT DEFAULT 'pending',
    -- pending, active, completed
    winner_id INTEGER,
    team1_ready BOOLEAN DEFAULT 0,
    team2_ready BOOLEAN DEFAULT 0,
    timer_start TIMESTAMP,
    team1_done BOOLEAN DEFAULT 0,
    team2_done BOOLEAN DEFAULT 0,
    team1_submitted_score1 INTEGER,
    team1_submitted_score2 INTEGER,
    team2_submitted_score1 INTEGER,
    team2_submitted_score2 INTEGER,
    mismatch_flag BOOLEAN DEFAULT 0,
    scheduled_start TIMESTAMP,
    notification_sent BOOLEAN DEFAULT 0,
    FOREIGN KEY (team1_id) REFERENCES teams (id),
    FOREIGN KEY (team2_id) REFERENCES teams (id),
    FOREIGN KEY (table_id) REFERENCES tables (id),
    FOREIGN KEY (winner_id) REFERENCES teams (id)
);
CREATE TABLE match_submissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id INTEGER NOT NULL,
    team_id INTEGER NOT NULL,
    score_for INTEGER NOT NULL,
    score_against INTEGER NOT NULL,
    submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(match_id, team_id),
    FOREIGN KEY (match_id) REFERENCES matches (id) ON DELETE CASCADE,
    FOREIGN KEY (team_id) REFERENCES teams (id) ON DELETE CASCADE
);
CREATE TABLE elo_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id INTEGER NOT NULL,
    elo INTEGER NOT NULL,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (team_id) REFERENCES teams (id)
);
INSERT INTO tables (name)
VALUES ('Table 1');