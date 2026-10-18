PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS departments (
 id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)));
CREATE TABLE IF NOT EXISTS teachers (
 id INTEGER PRIMARY KEY, name TEXT NOT NULL, department_id INTEGER NOT NULL REFERENCES departments(id),
 position TEXT NOT NULL, rate TEXT NOT NULL DEFAULT '1', active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)));
CREATE TABLE IF NOT EXISTS users (
 id INTEGER PRIMARY KEY, login TEXT NOT NULL UNIQUE COLLATE NOCASE, password TEXT NOT NULL,
 role TEXT NOT NULL CHECK(role IN ('admin','head','teacher')), teacher_id INTEGER REFERENCES teachers(id),
 department_id INTEGER REFERENCES departments(id), active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)));
CREATE TABLE IF NOT EXISTS worktypes (
 id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, unit TEXT NOT NULL CHECK(unit IN ('clock','academic')),
 active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)));
CREATE TABLE IF NOT EXISTS plans (
 id INTEGER PRIMARY KEY, teacher_id INTEGER NOT NULL REFERENCES teachers(id), period TEXT NOT NULL,
 type_id INTEGER NOT NULL REFERENCES worktypes(id), amount TEXT NOT NULL, note TEXT NOT NULL DEFAULT '',
 version INTEGER NOT NULL DEFAULT 1, UNIQUE(teacher_id,period,type_id));
CREATE TABLE IF NOT EXISTS entries (
 id INTEGER PRIMARY KEY, teacher_id INTEGER NOT NULL REFERENCES teachers(id), workday TEXT NOT NULL,
 start TEXT NOT NULL, end TEXT NOT NULL, minutes INTEGER NOT NULL CHECK(minutes>0 AND minutes<1440),
 type_id INTEGER NOT NULL REFERENCES worktypes(id), title TEXT NOT NULL, evidence TEXT NOT NULL DEFAULT '',
 status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','submitted','approved','returned')),
 created_by INTEGER NOT NULL REFERENCES users(id), updated_at TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1);
CREATE INDEX IF NOT EXISTS ix_entries_teacher_day ON entries(teacher_id,workday);
CREATE INDEX IF NOT EXISTS ix_entries_status ON entries(status,workday);
CREATE TABLE IF NOT EXISTS decisions (
 id INTEGER PRIMARY KEY, entry_id INTEGER NOT NULL REFERENCES entries(id), actor_id INTEGER NOT NULL REFERENCES users(id),
 previous TEXT NOT NULL, target TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS periods (
 period TEXT PRIMARY KEY, closed INTEGER NOT NULL DEFAULT 0 CHECK(closed IN (0,1)),
 actor_id INTEGER REFERENCES users(id), reason TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS settings (name TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS feedback (
 id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), subject TEXT NOT NULL,
 message TEXT NOT NULL, answer TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, answered_at TEXT);
CREATE TABLE IF NOT EXISTS audit (
 id INTEGER PRIMARY KEY, actor_id INTEGER REFERENCES users(id), event TEXT NOT NULL, entity TEXT NOT NULL,
 entity_id TEXT NOT NULL, details TEXT NOT NULL, created_at TEXT NOT NULL);
PRAGMA user_version=1;
