-- Core MVP schema for ProofLoop v0.1

CREATE TABLE user (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE experiment (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  focus_area TEXT NOT NULL,
  name TEXT NOT NULL,
  duration_days INTEGER NOT NULL DEFAULT 7,
  is_active BOOLEAN NOT NULL DEFAULT 1
);

CREATE TABLE user_experiment (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  experiment_id INTEGER NOT NULL,
  start_date DATE NOT NULL,
  end_date DATE NOT NULL,
  completed BOOLEAN NOT NULL DEFAULT 0,
  FOREIGN KEY (user_id) REFERENCES user(id),
  FOREIGN KEY (experiment_id) REFERENCES experiment(id)
);

CREATE TABLE check_in (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_experiment_id INTEGER NOT NULL,
  checkin_date DATE NOT NULL,
  score INTEGER NOT NULL CHECK(score >= 1 AND score <= 10),
  followed_experiment BOOLEAN NOT NULL,
  note TEXT,
  FOREIGN KEY (user_experiment_id) REFERENCES user_experiment(id),
  UNIQUE(user_experiment_id, checkin_date)
);
