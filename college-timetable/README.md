# MPSTME Timetable and Room Booking

A central, sign-in protected app for the timetable committee:

- **Find a room**: enter date, time, seats and room type; get the best free
  room and alternatives; book it in one click. Two people can never book the
  same room for the same hour: the database refuses the second one.
- **Generate the college timetable** for 8 AM to 6 PM, Monday to Saturday,
  from the teaching load and everyone's preferences.
- **Four timetables** from the same data: by class, teacher, department, room.
- **Import the existing workbooks as they are** (classrooms: one tab per day;
  labs: one tab per floor), and keep in step with the master Google Sheet.

The original small-scale demo is untouched in `../scheduling-app-pirate/`.

## Run it on your computer

```bash
cd college-timetable
pip install -r requirements.txt
streamlit run app.py
```

The first run asks you to create the admin account. Data is kept in
`college.db` in this folder. For a shared, hosted setup see [SETUP.md](SETUP.md).

To run the tests: `pip install pytest pdfplumber`, then `python -m pytest` in this folder.
The importer tests read the college workbooks from `tests/data/`
(`classrooms.xlsx`, `labs.xlsx`). Those files list staff names and emails, so
git ignores them and they are never pushed; without them those tests are skipped.

## First steps in the app

1. **Data > Room workbooks**: upload the classroom workbook and the lab
   workbook. Read the notes it shows, then save.
2. **Find a room** now works against the real occupancy.
3. **Data > Classes and teachers**: download the template, fill the eight
   tabs, upload. (Or load the sample college to try things out.)
4. **Generate timetable**: check, generate, review, publish.
5. **Users**: add coordinators and viewers.

## Running a real semester

- **Electives and open electives.** Fill the optional *Electives* tab of the
  inputs workbook. Every row with the same `block` runs at the same hours,
  each with its own teacher and a room sized for its `students`; every class
  listed in `class_groups` is kept free during the block. One division's
  department elective is a block that lists just that division; an open
  elective lists every division whose students choose from it. Blocks are
  placed first (late afternoon preferred, a different day for each hour)
  and are then fixed.
- **Edit by hand.** *Edit timetable* (admin): pick a class or teacher and a
  session, then move it to any of the legal times shown (with the free rooms
  that fit), or swap it with another session. The same rule checks as the
  generator are applied, so a hand edit cannot create a clash.
- **Lock.** Lock a class (or all of a class's sessions) to keep its day, time
  and room when the timetable is generated again. If a lock has become
  impossible (say the teacher is now unavailable), Generate says so and
  schedules that class normally.
- **Printable sheets.** On *Timetables*, choose Class or Teacher and download
  a PDF in the college's own layout (header, grid with merged lab blocks,
  break, subject and faculty legends, HOD and Dean lines), or one PDF with
  every class or every teacher.
- **Semesters.** *Data > Settings*: set the academic year, term, w.e.f. date
  and campus (used for semester numbers and PDF headers). *Start a new
  semester* archives the published timetable, then clears generated and
  hand-edited classes and the locks. Archived timetables stay downloadable.

## How the timetable is built

| Step | What happens | File |
|---|---|---|
| Check | Names anything impossible before starting (teacher with more hours than free time, class too big for any room) | `solver.check_feasible` |
| Construct | Places locked classes, then elective blocks, then every other class, most constrained first, in a room, with a reserved lunch hour per division | `solver.construct` |
| Polish | Google OR-Tools CP-SAT re-times one department-year at a time against everything else | `solver.improve` |
| Negotiate | Department agents ask for better slots and their own rooms; room agents rule on disputed rooms. Both learn with Expected SARSA (or Q-Learning) | `agents.negotiate` |
| Audit | An independent checker re-derives every rule from the result | `validate.audit` |

Rules that are never broken: one place at a time for every room, teacher and
class (a lab batch also clashes with its own division); enough seats and the
right kind of room; labs are two consecutive hours in one lab; classes only
on a group's campus days; teachers never when unavailable; one free lunch
hour between 12 and 3 for every class and teacher; existing bookings kept.

Preferences that are satisfied as far as possible: own-department rooms,
maximum days per week, Saturday, earliest and latest hours, teacher morning
or afternoon, compact days. Mark `must = yes` to make a row's preferences
unbreakable.

A must-have `max_days` applies to the whole class: the division and its lab
batches together come in on no more than that many days.

## Sample college

**Data > Classes and teachers > Load sample college** fills in a made-up
teaching load on the real rooms, so the generator can be tried before the
committee's sheets exist. Every programme runs all the years it has in
A.Y. 2026-27, odd semester:

| Programme | Years | Notes |
|---|---|---|
| B.Tech | 1 to 4 | year 1 belongs to the First Year department |
| MBA Tech | 1 to 5 | year 1 likewise; management subjects from year 3 |
| BTI | 4 to 6 | admissions stopped; the last batch finishes in 2029 |
| MCA | 1 to 2 | |
| Diploma | 1 to 3 | Computer, Mechanical, EXTC |
| M.Tech | 1 to 2 | Data Science, AI; small classes without lab batches |
| PhD | coursework | Research Methodology, Research and Publication Ethics |

Senior cohorts (B.Tech years 3 and 4, MBA Tech year 4, BTI year 5) each share
an open-elective block across departments, and final-year B.Tech Data Science
has a department elective. Semester numbers follow the term in Settings.

Older years ask for fewer days and no Saturday; final years ask for at most
4 days. One class is real: **9BTI DS** (BTI Data Science, Semester IX) has
its actual subjects and teachers, and must come in at most 3 days a week.
It takes part in the BTI year-5 open elective like everyone else.
Sizes: Small (Computer and Data Science), Medium (five departments, one
division each), Full college (all departments, 97 divisions).

Lunch is staggered by giving each division a reserved hour (12-1, 1-2 or
2-3) in rotation, so about a third of students eat in each hour.

## Measured on a college-sized test

Real room list (42 classrooms, 52 labs) with a made-up load of 87 divisions,
174 lab batches, 187 teachers and 2,349 class hours a week:

| Stage | Hours placed | Hours with no complaint | Penalty | Time |
|---|---|---|---|---|
| Construct | 2349 / 2349 | 95.7% | 3496 | about 2 s |
| + CP-SAT polish (60 s) | 2349 / 2349 | 97.1% | about 2440 | 60 s |
| + agent negotiation (150 rounds) | 2349 / 2349 | 97.9% | about 1970 | about 8 s |

Busiest lunch hour: 34% of students. Audit: zero violations. These numbers
come from made-up teaching loads on a 2-core machine; real loads will differ.

## Limits to know about

- The generator has not been run on a real MPSTME teaching load; none
  exists in the two workbooks.
- The canteen limit is reported against, not optimised for.
- Which department owns which room is guessed from the labels in the
  workbooks until a `DepartmentRooms` tab is uploaded.
- Sheet cells that say "booked" without a readable date stay blocked every
  week until an admin clears them under Data > Needs checking.
- Publishing a generated timetable replaces the weekly classes imported
  from the sheets.
- Live Google Sheet sync needs a hosted database (see SETUP.md); it has
  been tested here only by simulating the sheet's edits.

## Files

```
app.py            pages and navigation
db.py             tables, connection, the only write paths
importer.py       reads the classroom and lab workbooks
finder.py         room search, ranking, booking, cancelling
templates.py      the eight-tab input workbook and its checks
sample_data.py    a made-up college for trying the generator
solver.py         feasibility check, construction, CP-SAT polish
board.py          live timetable with every hard rule checked per move
agents.py         department and room agents, negotiation
validate.py       independent audit and scoring
views.py          the four timetables and Excel export
pdfs.py           printable timetables in the college format
editor.py         hand edits, swaps and locks
semester.py       semester settings, new semester, archives
auth.py           sign-in and roles
sync.py           sheet edit inbox and re-upload diff
sheet_script.gs   script to paste into the Google Sheet
theme.py          blue, red and black styling
tests/            94 tests; run with: python -m pytest tests
```

## OR-Tools will not load on Windows

If starting the app (or clicking Generate) shows `DLL load failed` or
`WinDLL(dll_path)` from `ortools`, Windows cannot load the solver's library.
The app still runs without it: the polish step is skipped and the timetable
is still complete and checked. To fix it:

1. Install the latest Microsoft Visual C++ Redistributable (x64) from
   https://aka.ms/vs/17/release/vc_redist.x64.exe, then close and reopen
   VS Code.
2. If it still fails, Anaconda's base environment is loading older copies of
   those files first. Use a clean environment instead:

   ```bash
   conda create -n timetable python=3.12 -y
   conda activate timetable
   cd college-timetable
   pip install -r requirements.txt
   streamlit run app.py
   ```

   In VS Code, choose that interpreter: Ctrl+Shift+P, "Python: Select
   Interpreter", then `timetable`.
