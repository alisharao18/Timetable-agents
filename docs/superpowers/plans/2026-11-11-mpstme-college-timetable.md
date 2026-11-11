# MPSTME College Timetable and Room Booking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A central, login-protected Streamlit app that builds the full MPSTME timetable and finds and books free rooms without double booking.

**Architecture:** One database (SQLite locally, Postgres when hosted) is the single source of truth; unique indexes make double booking impossible. A CP-SAT solver assigns times, a matcher assigns rooms, reinforcement-learning agents negotiate swaps, and an independent validator audits the result. The four timetables are queries over one `placements` table.

**Tech Stack:** Python 3.12+, Streamlit, SQLAlchemy 2 (Core), OR-Tools CP-SAT, pandas, openpyxl, NumPy, pytest.

**Spec:** `docs/superpowers/specs/2026-11-11-mpstme-college-timetable-design.md`

## Global Constraints

- All new code lives in `college-timetable/`; `scheduling-app-pirate/` is left untouched.
- `DAYS = ["Mon","Tue","Wed","Thu","Fri","Sat"]`.
- Timetable hours are start hours `8..17` (8 AM to 6 PM). Bookings may use `7..18`, matching the existing sheets.
- Lab sessions are two consecutive hours in one lab. Lunch window is start hours `12, 13, 14`.
- Roles: `admin`, `coordinator`, `viewer`.
- Database URL comes from env var `DATABASE_URL`, defaulting to `sqlite:///college.db`.
- Theme colours: navy `#0B2A5B`, red `#C8102E`, black `#111111`, white background.
- No code path writes a placement or booking except through `db.py`.

## Review Focus

1. A sheet cell that is neither a class code nor a readable date ("Booked full October"): the slot must stay blocked every week and appear in the unresolved list, never be offered as free. Test in Task 2.
2. Two people booking the same room, date and hour at once: exactly one succeeds, the other gets a clear message. Test in Task 3.
3. A batch lab and its parent division's lecture at the same hour: must be treated as a clash; two sibling batches at the same hour must not. Test in Task 5.
4. An impossible input (teacher with more hours than available slots): reported by name before solving, not as a silent partial timetable. Test in Task 5.
5. A negotiation swap that would break a hard rule: rejected, and the final timetable still passes the validator. Test in Task 7.

---

### Task 1: Database schema and access layer

**Files:** Create `college-timetable/db.py`, `college-timetable/constants.py`, `college-timetable/requirements.txt`; Test `college-timetable/tests/test_db.py`

**Interfaces — Produces:**
- `constants.DAYS`, `TT_HOURS = range(8, 18)`, `BOOK_HOURS = range(7, 19)`, `LUNCH_HOURS = (12, 13, 14)`
- `db.get_engine(url: str | None = None) -> Engine`, `db.init_db(engine) -> None`
- Tables: `rooms(id, name UNIQUE, kind['classroom'|'lab'], room_type, capacity, floor, owner_label, lab_type)`, `departments(id, name UNIQUE)`, `department_rooms(department_id, room_id, UNIQUE both)`, `class_groups(id, code UNIQUE, department_id, programme, year, size, parent_id)`, `teachers(id, name UNIQUE, department_id)`, `teaching_load(id, group_id, subject, teacher_id, hours, kind['lecture'|'lab'], lab_type)`, `group_prefs(group_id, day, on_campus, max_days, saturday_ok, earliest, latest, hard)`, `teacher_prefs(teacher_id, day, hour, available, max_days, hard)`, `placements(id, room_id, day, hour, group_id NULL, teacher_id NULL, subject, label, source, UNIQUE(room_id, day, hour))`, `bookings(id, room_id, date, hour, department, purpose, booked_by, created_at, UNIQUE(room_id, date, hour))`, `unresolved(id, room_id, day, hour, text)`, `users(id, email UNIQUE, pw_hash, role)`, `audit_log(id, at, user, action, detail)`, `settings(key PRIMARY KEY, value)`
- `db.q(engine, sql: str, **params) -> list[dict]`, `db.df(engine, sql, **params) -> DataFrame`
- `db.replace_placements(engine, rows: list[dict], source: str) -> None` (one transaction, deletes rows with that source first)

- [ ] Test: `init_db` twice is harmless; inserting two placements with the same `(room_id, day, hour)` raises `IntegrityError`; same for bookings on `(room_id, date, hour)`.
- [ ] Implement, run `pytest college-timetable/tests/test_db.py -v`, commit.

### Task 2: Importer for the two existing workbooks

**Files:** Create `college-timetable/importer.py`; Test `tests/test_importer.py` (uses copies of the real workbooks in `tests/data/`)

**Interfaces — Produces:**
- `importer.parse_classrooms(path) -> ImportResult`, `importer.parse_labs(path) -> ImportResult`
- `ImportResult` dataclass: `rooms: list[dict]`, `weekly: list[dict]` (room, day, hour, label), `dated: list[dict]` (room, date, hour, purpose), `unresolved: list[dict]` (room, day, hour, text), `notes: list[str]`
- `importer.classify_cell(text: str, year: int) -> tuple[str, date | None]` returning kind `'code' | 'dated' | 'unresolved' | 'empty'`
- `importer.load_into_db(engine, result: ImportResult) -> dict` (counts)

Decisions: classroom room list comes from the day tabs; rows are mapped by the first number in column A (`"12 :: 1"` is 12, `"1 :: 2"` is 13, `"5:6"` is 17). Lab sheets are scanned for header cells matching `E\.?\s*C[LC]\s*\d+` or a "Time / Monday…" header row, each starting a block; lab names are joined to the "Labs summary" tab on the lab number. Formula cells and cells with only whitespace are empty. `dated` text matches `dd.mm.yyyy` or `d Mon`. Unresolved cells also create a weekly placement with `source='unresolved'` so the slot stays blocked.

- [ ] Test: 42 classrooms parsed with capacities; `E.CR.101` Monday hour 8 is `"1B DS 2"`; `"Booked on 26.10.2026"` becomes a dated booking on 2026-10-26; `"Booked full October"` is unresolved and blocked; at least 30 labs parsed, `E. CL 501` Monday 8 is `"3B IT"`; every non-empty grid cell is counted in exactly one of weekly, dated, unresolved.
- [ ] Implement, run tests, commit.

### Task 3: Room finder and booking

**Files:** Create `college-timetable/finder.py`; Test `tests/test_finder.py`

**Interfaces — Produces:**
- `finder.find_rooms(engine, on: date, start_hour: int, duration: int, kind: str, seats: int, room_type: str | None = None, lab_type: str | None = None, department: str | None = None) -> list[dict]` ranked; each dict has `room, capacity, room_type, floor, own_department: bool, spare_seats`
- `finder.nearest_free(engine, ...same..., limit=5) -> list[dict]` (other start hours that day, then following days)
- `finder.book(engine, room: str, on: date, start_hour: int, duration: int, department: str, purpose: str, user: str) -> tuple[bool, str]`
- `finder.cancel(engine, booking_id: int, user: str, is_admin: bool) -> tuple[bool, str]`

A room is free when it has no placement for that weekday and hour and no booking for that date and hour, for every hour of the duration. Ranking: own department, then fewest spare seats, then lowest floor. `book` inserts all hours in one transaction and returns `(False, "… was just booked by someone else")` on `IntegrityError`.

- [ ] Test: a room with a weekly placement is not offered; a dated booking blocks only that date; ranking order; two threads calling `book` for the same slot give exactly one `True`; cancel by a different non-admin user is refused.
- [ ] Implement, run tests, commit.

### Task 4: Input templates and validation

**Files:** Create `college-timetable/templates.py`, `college-timetable/sample_data.py`; Test `tests/test_templates.py`

**Interfaces — Produces:**
- `templates.build_template_workbook() -> bytes` with sheets `Departments`, `DepartmentRooms`, `ClassGroups`, `Teachers`, `TeachingLoad`, `ClassPreferences`, `TeacherPreferences`, each with headers and one example row
- `templates.load_workbook_into_db(engine, file) -> list[str]` (problems found; nothing is written if the list is non-empty)
- `sample_data.make_college(engine, seed=0, scale=1.0) -> None`: synthetic college sized from the real room list (about 110 class groups, 20–24 hours each)

- [ ] Test: round trip of the template; unknown teacher or room in a sheet is reported with sheet and row number and nothing is written; `make_college` produces load that fits the room-hours available.
- [ ] Implement, run tests, commit.

### Task 5: Solver (feasibility check, times, rooms)

**Files:** Create `college-timetable/solver.py`; Test `tests/test_solver.py`

**Interfaces — Produces:**
- `solver.load_problem(engine) -> Problem` (dataclass of plain lists and dicts; no database access after this)
- `solver.check_feasible(p: Problem) -> list[str]`
- `solver.solve_times(p: Problem, time_limit_s: int = 120) -> dict[int, tuple[str, int]]` (session id to day, start hour)
- `solver.assign_rooms(p: Problem, times) -> tuple[dict[int, int], list[dict]]` (session to room id; unplaced with reason)
- `solver.to_rows(p, times, rooms) -> list[dict]` for `db.replace_placements`

Model: one session per lecture hour, one per two-hour lab. Boolean per session and allowed start. At most one session per teacher per hour; at most one per group per hour, where a group clashes with its parent and its children but not its siblings. Per hour, sessions needing more than `c` seats must not exceed rooms with more than `c` seats, for each distinct capacity `c`; the same per lab type. Lunch: for each group and day, a lunch-hour choice among `LUNCH_HOURS` that must be a free hour; the objective penalises the largest student count at lunch in any one hour, above the `canteen_limit` setting most heavily. Teachers get a free hour in the window. Soft terms: days used above `max_days`, Saturday use when `saturday_ok` is false, hours outside earliest and latest, gaps. Preferences with `hard = true` become constraints. Room assignment: labs first, then per hour largest class first into the smallest fitting free room, own-department rooms preferred.

- [ ] Test: on a 12-group sample, zero clashes and every session placed; batch and parent clash rule (Review Focus 3); an overloaded teacher is named by `check_feasible` (Review Focus 4); every group has a free lunch hour on every day and no single lunch hour holds more than 45% of students; a group marked off campus on Saturday has no Saturday session.
- [ ] Implement, run tests, commit.

### Task 6: Validator and scoring

**Files:** Create `college-timetable/validate.py`; Test `tests/test_validate.py`

**Interfaces — Produces:**
- `validate.audit(p: Problem, rows: list[dict]) -> list[str]` (hard-rule violations, re-derived from rows only)
- `validate.score(p: Problem, rows: list[dict]) -> dict` with keys `preference`, `fairness_min`, `own_room_share`, `lunch_peak_share`, `gaps`

- [ ] Test: a clean solver result audits to `[]`; hand-made rows with a teacher clash, an oversize class, a split lab and a missing lunch each produce one message.
- [ ] Implement, run tests, commit.

### Task 7: Agent negotiation

**Files:** Create `college-timetable/agents.py`; Test `tests/test_agents.py`

**Interfaces — Consumes:** `Problem`, rows, `validate.audit`, `validate.score`. **Produces:**
- `agents.negotiate(p: Problem, rows: list[dict], rounds: int = 200, algo: str = "expected_sarsa", seed: int = 0) -> tuple[list[dict], dict]` (improved rows; report with `before`, `after`, `accepted`, `rejected`, `per_dept`)

One `DeptAgent` per department with a tabular Q over state (own satisfaction bucket, progress bucket, last outcome) and actions `move_worst`, `swap_inside`, `claim_own_room`, `ask_room_swap`, `hold`. One `RoomAgent` per floor arbitrates competing claims on the same cell by choosing a rule (best fit, biggest gain, least satisfied department). Expected SARSA updates by default, Q-learning selectable. A move is applied only if an incremental legality check passes; the best-scoring timetable seen is returned.

- [ ] Test: result audits to `[]` (Review Focus 5); `after` score is not lower than `before`; same seed gives the same result; a forced illegal move is counted in `rejected` and leaves rows unchanged.
- [ ] Implement, run tests, commit.

### Task 8: Views and exports

**Files:** Create `college-timetable/views.py`; Test `tests/test_views.py`

**Interfaces — Produces:**
- `views.grid(engine, by: str, key: str) -> DataFrame` with `by` in `'student' | 'teacher' | 'department' | 'room'`; index hours, columns days
- `views.keys(engine, by: str) -> list[str]`
- `views.export_excel(engine, by: str) -> bytes` (one sheet per key)

The student view of a division includes its batches' labs; the room view includes dated bookings for a chosen week.

- [ ] Test: each cell of the four grids matches the placements rows; the teacher grid for a teacher with 12 hours has 12 filled hours.
- [ ] Implement, run tests, commit.

### Task 9: Login, sheet sync, theme

**Files:** Create `college-timetable/auth.py`, `college-timetable/sync.py`, `college-timetable/theme.py`, `college-timetable/sheet_script.gs`, `.streamlit/config.toml` (modify); Test `tests/test_auth.py`, `tests/test_sync.py`

**Interfaces — Produces:**
- `auth.create_user(engine, email, password, role)`, `auth.check_login(engine, email, password) -> dict | None`, `auth.ensure_admin(engine)` (from env `ADMIN_EMAIL`, `ADMIN_PASSWORD`), `auth.can(role: str, action: str) -> bool`; PBKDF2-SHA256 with per-user salt
- Table `sheet_edits(id, at, sheet, room, day, hour, old_text, new_text, editor, status, note)` added to `db.init_db`
- `sync.process_inbox(engine) -> dict` applies pending edits through the same cell classifier as the importer; a clash sets `status='rejected'` with a note
- `sync.diff_upload(engine, result: ImportResult) -> DataFrame` for Excel re-upload
- `sheet_script.gs`: `onEdit` posts the edit to the database's REST endpoint for `sheet_edits`
- `theme.inject_theme()`, `theme.header(title, subtitle)`

- [ ] Test: wrong password fails; viewer cannot `book`; an inbox edit on a free cell is applied; one on a booked cell is rejected and the original stays.
- [ ] Implement, run tests, commit.

### Task 10: App, setup guide, end-to-end check

**Files:** Create `college-timetable/app.py`, `college-timetable/SETUP.md`, `college-timetable/README.md`; modify root `README.md`

Pages: Sign in; Find a room; Timetables (four views, downloads); Bookings (mine, all, cancel); Data (import workbooks, templates, unresolved cells, sheet edits) for admin; Generate (check, solve, negotiate, audit, publish) for admin; Users for admin.

- [ ] Run the full pipeline on the real workbooks plus sample college data: import, generate, audit `[]`, book a room, confirm it disappears from the finder.
- [ ] Start the app headless and load each page with Playwright; no exceptions on any page; screenshot the finder and one timetable view.
- [ ] Write `SETUP.md` (Supabase database, secrets, sheet script, Streamlit deployment), commit.
