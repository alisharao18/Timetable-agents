# Real-college additions: design

Date: 12 November 2026. Extends `2026-11-11-mpstme-college-timetable-design.md`.

## 1. Manual edits and locks
- Every class session has a stable key `L<load id>.<n>` (n = 0, 1, ... within a load row).
- Edit page (admin): pick a class or teacher, pick a session, see every legal
  new time with the free rooms that fit, move it, or swap it with another
  session of the same class. The same rule checks as the generator are used;
  an illegal move is refused with the reason.
- Moved sessions are saved with source `manual`.
- A lock pins a session's day, hour and room. Locks are stored by class code,
  subject, teacher and n, so they survive re-uploading the inputs. The
  generator places locked sessions first and never moves them; a lock that
  is no longer legal is reported and that session is scheduled normally.

## 2. Electives and open electives
- New input tab `Electives`: block, class_groups, subject, teacher, students,
  hours_per_week, type, lab_type.
- All rows with the same block run at the same hours, each with its own
  teacher and room sized for its own students. Every class group listed is
  busy during the block.
- One division's department elective = a block listing that division.
  An open elective = a block listing many divisions across departments.
- Blocks are placed before ordinary classes, preferring late-afternoon hours
  and different days for each hour of the block, then are fixed.
- Rules: rows in a block must share hours and type; a teacher may appear
  only once per block.

## 3. College-format PDF timetables
- Per class and per teacher, A4 landscape, matching the college's sheet:
  NMIMS and MPSTME header, campus and academic year, course, semester and
  w.e.f. date; Monday-Saturday by hour grid with 2-hour labs merged; lunch
  shown as Break; subject legend (name, initial) and faculty legend (name,
  initial); HOD and Dean signature lines.
- Initials: subject from capital letters of significant words; faculty from
  first letter of first name + first two of surname, with fallbacks so no
  two teachers share one.
- Download one, or all classes / all teachers as one PDF.

## 4. Semesters and other programmes
- Settings: academic year, term (Odd or Even), w.e.f. date, campus. Used in
  PDF headers, semester numbers and the importer's default year.
- Start a new semester: the published timetable is archived (downloadable
  later), then generated and manual classes and locks are cleared.
- Sample college adds Diploma (3 years), M.Tech (2 years) and PhD
  coursework, open-elective blocks for senior cohorts, and one department
  elective; semester numbers follow the term.

## Database changes
- teaching_load: block, students. New tables: elective_members, locks, archives.
- placements: block.
- `init_db` adds missing columns to an existing database, so an older
  `college.db` keeps working.
