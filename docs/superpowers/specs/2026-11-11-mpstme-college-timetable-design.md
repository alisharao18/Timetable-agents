# MPSTME College Timetable and Room Booking: Design

Date: 11 November 2026
Status: approved 11 November 2026, with staggered lunch added

## 1. Purpose

Turn the multi-agent scheduling demo into a system the MPSTME timetable
committee can use to:

1. Build the full college timetable for a semester.
2. Find and book a free classroom or lab for a one-off need, with no chance
   of two people taking the same room.

It replaces the current routine of typing into a shared Excel sheet and then
emailing the coordinator.

## 2. Who uses it

| Role | Can do |
|---|---|
| Admin (timetable committee) | Everything: import data, generate and publish the timetable, manage users |
| Coordinator (per department) | Find and book rooms, view all timetables |
| Viewer (teachers, others) | View timetables only |

## 3. What the committee provides

Each item can be uploaded as a sheet or typed into the app.

| Input | Contents | Source today |
|---|---|---|
| Rooms and labs | Room number, capacity, type (STEP, FLAT, lab type), floor, owning department | The two existing workbooks, imported as they are |
| Department sheet | Each department's programmes, the rooms and labs it is authorised to use, and its teachers | New template |
| Class groups | Programme, year, division, batch, student count (e.g. "3B CS B", 68) | Codes tab plus counts |
| Teaching load | Class group, subject, teacher, hours per week, lecture or lab | New template; not in either existing workbook |
| Class preferences | Per programme and year: days on campus, maximum days per week, Saturday yes or no, earliest start, latest finish | New template |
| Teacher preferences | Per teacher: unavailable days and hours, maximum days per week, preferred times | New template |

## 4. Rules the timetable must obey

Hard rules (never broken):

- A room, a teacher and a class group are each in one place at a time.
- The room has enough seats and is the right type (a lab session goes in a
  lab of the right kind).
- Classes run between 8 AM and 6 PM, Monday to Saturday, in one-hour slots.
- A lab session occupies two consecutive hours in the same lab.
- A class group is scheduled only on days marked as on campus for it.
- A teacher is never scheduled when marked unavailable.
- Dated one-off bookings already in the database are respected.
- Every class group has one free hour for lunch between 12 PM and 3 PM on
  any day its classes run across that window.

Soft rules (satisfied as far as possible, and scored):

- Classes go in the department's own authorised rooms first.
- Maximum days per week for a class group or teacher.
- Saturday preference, preferred times, earliest start and latest finish.
- Few gaps in a class group's day.
- Lunch hours are staggered: the number of students free in each of the
  three lunch hours (12-1, 1-2, 2-3) is kept as even as possible, and under
  a canteen limit the committee can set. Teachers get a lunch hour in the
  same window.
- Fair spread of good and bad slots across departments.

The committee can promote any soft rule to a hard rule for a given class
group or teacher.

## 5. How the timetable is built (hybrid)

1. **Check.** Before running, report anything impossible, such as a teacher
   with more hours than free slots, or a class too large for any room.
2. **Solve times.** A constraint solver (Google OR-Tools) assigns a day and
   hour to every session so that no teacher or class group clashes and each
   hour needs no more rooms of each type than exist.
3. **Assign rooms.** For each hour, sessions are matched to rooms,
   own-department rooms first.
4. **Negotiate.** Department agents and room agents (the existing
   reinforcement-learning agents, reworked for the real grid) negotiate
   swaps over contested slots and rooms to raise preference and fairness
   scores. A swap is accepted only if every hard rule still holds.
5. **Audit.** An independent validator re-checks the whole timetable.
6. **Publish.** The admin reviews the result and the list of anything
   unplaced, with a plain reason for each, then publishes.

The app reports the scores before and after negotiation, so the agents'
contribution is measurable.

## 6. Four timetable views

All four are views of the same stored placements, so they cannot disagree.

| View | Shows | For |
|---|---|---|
| Student | One class group's week | Students, class teachers |
| Teacher | One teacher's week | Teachers |
| Department | All classes of a department's programmes | Heads of department |
| Room | One room's or lab's week, plus dated bookings | Committee, coordinators |

Each view can be downloaded as Excel or CSV.

## 7. Room finder and booking

- Input: date, start time, duration, room type, seats, requesting department,
  purpose.
- Output: the best free room, then ranked alternatives. Ranking: own
  department's rooms, then smallest room that fits, then nearest floor.
- If nothing is free, it suggests the nearest free times for the same need.
- One click books the room. The booking is tied to that date only and records
  who booked and when. Bookings can be cancelled by the booker or an admin.

## 8. Central database

- Hosted Postgres (Supabase or Neon free tier), so data survives app restarts
  and everyone sees the same state.
- Tables: rooms, departments, department_rooms, programmes, class_groups,
  teachers, teaching_load, preferences, placements (weekly timetable),
  bookings (dated), users, audit_log.
- The database enforces uniqueness on room + day + hour for placements and
  room + date + hour for bookings. A second attempt fails cleanly, even if
  two people click at the same moment.
- A local SQLite mode with the same schema is used for development and tests.

## 9. Sheet sync

- First import: reads the two existing workbooks in their current layout,
  including the per-floor lab blocks. Free-text cells such as
  "Booked on 26.10.2026" become dated bookings; unreadable cells are listed
  for a person to resolve rather than guessed.
- Ongoing, if the master is a Google Sheet: a script on the sheet sends each
  edit to the database within seconds. An edit that clashes with an existing
  booking is rejected and listed for the coordinator.
- If the master is an Excel file: re-upload, with a preview of what changed.
- The app writes the current timetable to a read-only tab or export for
  people who prefer the Excel view.

Known data issue: the "CR details" tab and the day tabs list different rooms.
The importer uses the day tabs as the room list and reports the difference.

## 10. Login and theme

- Email and password login with the three roles in section 2. User accounts
  are created by an admin.
- Theme: navy blue primary, red for alerts and accents, black text on white.
  No animations or mascots.

## 11. Code layout

```
app.py              Streamlit pages and navigation
db.py               schema, connection, locking-safe booking writes
importer.py         reads the existing classroom and lab workbooks
templates.py        blank input sheets and their validation
sync.py             Google Sheet edit receiver and Excel re-upload diff
solver.py           feasibility check, time solving, room assignment
agents.py           reworked department and room agents, negotiation step
validate.py         independent audit of any timetable
finder.py           room search, ranking, booking
views.py            the four timetable views and exports
auth.py             login and roles
theme.py            blue, red and black styling
sheet_script.gs     script to paste into the Google Sheet
SETUP.md            database, sheet script and deployment steps
tests/              importer, solver, validator, finder, booking races
```

The existing `scheduler.py` and trained agents stay in the repo as the
original small-scale demo.

## 12. Testing

- Importer: run against the two real workbooks; every room and every filled
  cell is accounted for.
- Solver and validator: synthetic college-sized data, since no real teaching
  load exists yet; zero hard-rule violations required.
- Booking: two simultaneous requests for the same slot; exactly one succeeds.
- Finder: results checked against the imported real occupancy.

## 13. Not included

- Exam timetables and invigilation.
- Sending email or calendar invites.
- A live connection to the college's Google Sheet or a hosted database. Both
  need the owners' accounts; the code and setup steps are provided.

## 14. Build order

1. Database and importer.
2. Room finder and booking.
3. Input templates, solver, validator, four views.
4. Agent negotiation step.
5. Login, theme, sheet sync, setup guide.
