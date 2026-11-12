"""A made-up but college-sized teaching load, for trying the generator
before the real load sheet exists. Uses the rooms already in the database
(from the workbook import); creates stand-in rooms if there are none.

Every programme runs all the years it has in A.Y. 2026-27:
  B.Tech    4 years (year 1 is taught by the First Year department)
  MBA Tech  5 years (year 1 likewise)
  BTI       years 4, 5 and 6 only: admissions have stopped, last batch ends 2029
  MCA       2 years
  Diploma   3 years;  M.Tech  2 years;  PhD coursework  1 year
Senior cohorts share an open-elective block across departments, and final-
year B.Tech Data Science has a department elective. Semester numbers follow
the term in the semester settings (Odd: I, III, V ...; Even: II, IV, VI ...).

One class is real: 9BTI DS (BTI Data Science, year 5), with the subjects and
teachers from its actual timetable, and a must-have limit of 3 days a week.
"""
import random

import pandas as pd
from sqlalchemy import insert

import db
import semester
import templates

PROGRAMME_YEARS = {"B.Tech": [1, 2, 3, 4], "MBA Tech": [1, 2, 3, 4, 5], "BTI": [4, 5, 6], "MCA": [1, 2],
                   "Diploma": [1, 2, 3], "M.Tech": [1, 2], "PhD": [1]}
FINAL_YEAR = {"B.Tech": 4, "MBA Tech": 5, "BTI": 6, "MCA": 2, "Diploma": 3, "M.Tech": 2, "PhD": 1}
CODE_LETTER = {"B.Tech": "B", "MBA Tech": "M", "BTI": "BTI", "MCA": "MCA", "Diploma": "D",
               "M.Tech": "MT", "PhD": "PhD"}
DIPLOMA = ["Computer", "Mechanical", "EXTC"]          # departments that run a diploma
MTECH = ["Data Science", "AI"]                        # departments that run an M.Tech
# open-elective cohorts: (programme, year) whose divisions choose from one shared block
OE_COHORTS = [("B.Tech", 3), ("B.Tech", 4), ("MBA Tech", 4), ("BTI", 5)]
OE_SUBJECTS = ["Design Thinking", "Financial Literacy", "Psychology", "German", "Japanese",
               "Indian Knowledge Systems", "Creative Writing", "Photography", "Entrepreneurship",
               "Public Policy", "Sustainable Development", "Music Appreciation", "Digital Marketing",
               "Business Analytics", "Film Studies", "Law for Engineers"]

# department, short code, divisions per year in B.Tech, MBA Tech, BTI
DEPARTMENTS = [
    ("Computer", "CS", 3, 2, 1), ("IT", "IT", 2, 1, 1), ("Data Science", "DS", 2, 1, 1),
    ("AI", "AI", 1, 1, 0), ("Mechanical", "ME", 1, 1, 1), ("Mechatronics", "MX", 1, 0, 0),
    ("Civil", "CI", 1, 0, 0), ("EXTC", "EXTC", 1, 1, 0),
]
# which departments each sample size uses (every programme and year is always present)
SIZES = {"tiny": ["Data Science"], "small": ["Computer", "Data Science"],
         "medium": ["Computer", "IT", "Data Science", "AI", "Mechanical"]}

COMPUTING = ["Data Structures", "Operating Systems", "Computer Networks", "Database Systems",
             "Machine Learning", "Software Engineering", "Cloud Computing", "Web Programming",
             "Statistics", "Deep Learning", "Big Data Analytics", "Cyber Security"]
SUBJECT_POOLS = {
    "Computer": COMPUTING, "IT": COMPUTING, "Data Science": COMPUTING, "AI": COMPUTING,
    "Mechanical": ["Thermodynamics", "Fluid Mechanics", "Machine Design", "Manufacturing",
                   "Heat Transfer", "CAD/CAM", "Industrial Engineering", "Vibrations"],
    "Mechatronics": ["Control Systems", "Robotics", "Sensors and Actuators", "Embedded Systems",
                     "Hydraulics and Pneumatics", "Machine Design", "PLC and SCADA"],
    "Civil": ["Structural Analysis", "Surveying", "Geotechnical Engineering", "Concrete Technology",
              "Transportation Engineering", "Water Resources", "Construction Management"],
    "EXTC": ["Signals and Systems", "Digital Electronics", "Communication Systems", "VLSI Design",
             "Microprocessors", "Antennas and Propagation", "Digital Signal Processing"],
    "First Year": ["Engineering Mathematics", "Engineering Physics", "Engineering Chemistry",
                   "Programming in C", "Engineering Drawing", "Communication Skills", "Basic Electrical"],
    "MCA": ["Java Programming", "Data Structures", "Database Systems", "Web Technologies",
            "Python Programming", "Software Testing"],
}
MANAGEMENT = ["Marketing Management", "Financial Management", "Operations Management",
              "Managerial Economics", "Organisational Behaviour"]

# The real 9BTI DS (Semester IX) timetable: subject, teacher, hours, type, who
REAL_CODE = "9BTI DS"
REAL_SIZE = 60
REAL_LOAD = [
    ("Applied Time Series Analysis", "Prof. Saurabh Pandit", 2, "lecture", "div"),
    ("Applied Time Series Analysis Lab", "Prof. Saurabh Pandit", 2, "lab", "B1"),
    ("Applied Time Series Analysis Lab", "Prof. Saurabh Pandit", 2, "lab", "B2"),
    ("Foundations of Machine Learning", "Dr. Siba Panda", 3, "lecture", "div"),
    ("Foundations of Machine Learning Lab", "Dr. Siba Panda", 2, "lab", "B1"),
    ("Foundations of Machine Learning Lab", "Dr. Siba Panda", 2, "lab", "B2"),
    ("Applied AI and Agentic Systems", "Dr. Vinita Mane", 2, "lecture", "div"),
    ("Applied AI and Agentic Systems Lab", "Dr. Vinita Mane", 2, "lab", "B1"),
    ("Applied AI and Agentic Systems Lab", "Dr. Vinita Mane", 2, "lab", "B2"),
    ("Data Driven Program Analysis Lab", "Prof. Atharva Rode", 2, "lab", "B1"),
    ("Data Driven Program Analysis Lab", "Dr. JayKrishna Joshi", 2, "lab", "B2"),
    ("Virtualization and Cloud Computing", "Prof. Soni Bhambar", 2, "lecture", "div"),
    ("Virtualization and Cloud Computing Lab", "Prof. Soni Bhambar", 2, "lab", "B1"),
    ("Virtualization and Cloud Computing Lab", "Prof. Soni Bhambar", 2, "lab", "B2"),
    ("Intro to Modern Application Development", "Prof. Snehee Chheda", 1, "lecture", "div"),
    ("Intro to Modern Application Development (2)", "Prof. Anish Gharat", 1, "lecture", "div"),
    ("Modern Application Development Practical", "Prof. Snehee Chheda", 2, "lecture", "div"),
]


def _standin_rooms(engine, n_class, n_lab):
    rows = [dict(name=f"CR-{i+1}", kind="classroom", room_type="STEP" if i % 3 else "FLAT",
                 capacity=[64, 68, 72, 82][i % 4], floor=1 + i % 8, owner_label="", lab_type=None)
            for i in range(n_class)]
    rows += [dict(name=f"LAB-{i+1}", kind="lab", room_type="LAB", capacity=30 + 2 * (i % 3),
                  floor=1 + i % 8, owner_label="", lab_type="COMPUTER") for i in range(n_lab)]
    with engine.begin() as c:
        c.execute(insert(db.rooms), rows)


def _size_name(scale):
    if scale >= 1.0:
        return "full"
    if scale >= 0.5:
        return "medium"
    return "small" if scale >= 0.25 else "tiny"


def _divisions():
    """Yield (department of the class, branch dept, short, programme, year, n_div)."""
    for dept, short, n_b, n_m, n_bti in DEPARTMENTS:
        for prog, n in (("B.Tech", n_b), ("MBA Tech", n_m), ("BTI", n_bti),
                        ("Diploma", int(dept in DIPLOMA)), ("M.Tech", int(dept in MTECH))):
            if not n:
                continue
            for year in PROGRAMME_YEARS[prog]:
                owner = "First Year" if year == 1 and prog in ("B.Tech", "MBA Tech") else dept
                yield owner, dept, short, prog, year, n
    for year in PROGRAMME_YEARS["MCA"]:
        yield "MCA", "MCA", "", "MCA", year, 1
    yield "Research", "Research", "", "PhD", 1, 1


def make_frames(engine, seed=0, scale=1.0):
    rng = random.Random(seed)
    sem = semester.get(engine)
    size = _size_name(scale)
    keep = None if size == "full" else set(SIZES[size]) | {"MCA", "Research"} if size != "tiny" else set(SIZES[size])
    groups, load, gprefs, teachers, tprefs, depts, electives = [], [], [], [], [], [], []
    cohort = {}                                              # (programme, year) -> [(code, size)]

    def add_dept(name):
        if name not in depts:
            depts.append(name)

    for owner, branch, short, prog, year, n_div in _divisions():
        if keep is not None and branch not in keep:
            continue
        n_div = n_div if size == "full" else 1
        num = semester.sem_number(sem, year)
        for d in range(n_div):
            code = f"{num}{CODE_LETTER[prog]}" + (f" {short}" if short else "") + \
                   (f" {chr(65 + d)}" if n_div > 1 else "")
            add_dept(owner)
            if code == REAL_CODE or (prog == "BTI" and branch == "Data Science" and year == 5):
                _add_real(groups, load, gprefs, owner, prog, year, code)
                cohort.setdefault((prog, year), []).append((code, REAL_SIZE))
                continue
            if prog == "PhD":
                groups.append([code + " Coursework", owner, prog, year, 12, ""])
                load += [[code + " Coursework", "Research Methodology", owner, 3, "lecture", ""],
                         [code + " Coursework", "Research and Publication Ethics", owner, 2, "lecture", ""]]
                gprefs.append([code + " Coursework", "", 2, "yes", 9, 17, "no"])
                continue
            small = prog in ("M.Tech",)
            div_size = rng.randint(18, 24) if small else rng.randint(40, 50) if prog == "Diploma" \
                else rng.randint(54, 62)
            groups.append([code, owner, prog, year, div_size, ""])
            cohort.setdefault((prog, year), []).append((code, div_size))
            if not small:
                b1 = div_size // 2
                for b, bs in (("B1", b1), ("B2", div_size - b1)):
                    groups.append([f"{code}-{b}", owner, prog, year, bs, code])
            pool = SUBJECT_POOLS["First Year" if year == 1 and prog in ("B.Tech", "MBA Tech") else branch]
            subs = rng.sample(pool, 4)
            if prog == "MBA Tech" and year >= 3:
                subs.append(rng.choice(MANAGEMENT))
            else:
                subs.append(rng.choice([s for s in pool if s not in subs]))
            if prog == "B.Tech" and branch == "Data Science" and year == 4:
                subs = subs[:4]                                  # one subject becomes an elective
                electives += [[f"DE {code}", code, title, f"Prof. DS E{i + 1}", div_size // 2 + 1, 3,
                               "lecture", ""] for i, title in enumerate(["Natural Language Processing",
                                                                          "Computer Vision"])]
            for s in subs:
                load.append([code, s, owner, 3, "lecture", ""])
            for s in subs[:3]:
                for b in (("B1", "B2") if not small else ("",)):
                    load.append([f"{code}-{b}" if b else code, f"{s} Lab", owner, 2, "lab", "COMPUTER"])
            gprefs.append(_class_prefs(rng, prog, year, code))

    # open electives: every division of a senior cohort chooses from one shared block
    n_oe = 0
    for prog, year in OE_COHORTS:
        members = cohort.get((prog, year), [])
        if len(members) < 2:
            continue
        total = sum(sz for _, sz in members)
        n_opt = max(2, -(-total // 60))
        name = f"OE {semester.roman(semester.sem_number(sem, year))} {prog}"
        codes = ",".join(c for c, _ in members)
        for i in range(n_opt):
            electives.append([name, codes, OE_SUBJECTS[i % len(OE_SUBJECTS)], f"Prof. OE {i + 1}",
                              -(-total // n_opt), 2, "lecture", ""])
        n_oe = max(n_oe, n_opt)

    # stand-in teachers: enough per department that nobody exceeds ~13 hours a week
    by_dept = {}
    for row in load:
        if row[2] in depts:                                 # real rows already name a teacher
            by_dept.setdefault(row[2], []).append(row)
    for dept, rows in by_dept.items():
        hours = sum(r[3] for r in rows)
        tag = {"First Year": "FY", "Data Science": "DS", "Mechanical": "ME", "Mechatronics": "MX",
               "Civil": "CI", "Computer": "CS"}.get(dept, dept)
        names = [f"Prof. {tag} {i + 1}" for i in range(max(2, -(-hours // 13)))]
        got = {n: 0 for n in names}
        for r in sorted(rows, key=lambda r: (r[1], r[0])):
            n = min(names, key=lambda n: (got[n], rng.random()))
            got[n] += r[3]
            r[2] = n
        for n in names:
            teachers.append([n, dept])
            roll = rng.random()
            tprefs.append([n, "Sat" if roll < 0.5 else (rng.choice(["Mon", "Fri"]) if roll < 0.6 else ""),
                           "", 5 if roll < 0.8 else 4, rng.choice(["morning", "afternoon", ""]), "no"])
    if any(g[0] == _real_code(sem) for g in groups):
        for name in sorted({t for _, t, _, _, _ in REAL_LOAD}):
            teachers.append([name, "Data Science"])
    if n_oe:
        add_dept("Humanities and Management")
        teachers += [[f"Prof. OE {i + 1}", "Humanities and Management"] for i in range(n_oe)]
    if any(e[0].startswith("DE ") for e in electives):
        teachers += [["Prof. DS E1", "Data Science"], ["Prof. DS E2", "Data Science"]]

    f = lambda name, rows: pd.DataFrame(rows, columns=templates.SHEETS[name][0])
    return {"Departments": f("Departments", [[d] for d in depts]),
            "DepartmentRooms": f("DepartmentRooms", []),
            "ClassGroups": f("ClassGroups", groups), "Teachers": f("Teachers", teachers),
            "TeachingLoad": f("TeachingLoad", load), "ClassPreferences": f("ClassPreferences", gprefs),
            "TeacherPreferences": f("TeacherPreferences", tprefs),
            "Electives": f("Electives", electives)}


def _class_prefs(rng, prog, year, code):
    """Older years want fewer days and no Saturday; first years come all week."""
    final = year == FINAL_YEAR[prog]
    senior = year >= 3
    if final:
        max_days = 4
    elif senior:
        max_days = 4 if rng.random() < 0.5 else 5
    else:
        max_days = ""
    likes_saturday = (not senior) or rng.random() < 0.2
    days = "Mon,Tue,Wed,Thu,Fri" if senior and not likes_saturday and rng.random() < 0.6 else ""
    return [code, days, max_days, "yes" if likes_saturday else "no", 8,
            17 if senior else 18, "no"]


def _real_code(sem):
    """The real class is BTI Data Science year 5: 9BTI DS in an Odd term, 10BTI DS in an Even one."""
    return f"{semester.sem_number(sem, 5)}BTI DS"


def _add_real(groups, load, gprefs, owner, prog, year, code=REAL_CODE):
    groups.append([code, owner, prog, year, REAL_SIZE, ""])
    for b in ("B1", "B2"):
        groups.append([f"{code}-{b}", owner, prog, year, REAL_SIZE // 2, code])
    for subject, teacher, hours, kind, who in REAL_LOAD:
        target = code if who == "div" else f"{code}-{who}"
        load.append([target, subject, teacher, hours, kind, "COMPUTER" if kind == "lab" else ""])
    # at most 3 days a week, and that is a must
    gprefs.append([code, "", 3, "yes", 8, 18, "yes"])


def make_college(engine, seed=0, scale=1.0):
    """Fill the database with a sample college. -> problems (should be [])."""
    frames = make_frames(engine, seed, scale)
    if not db.q(engine, "select 1 from rooms limit 1"):
        n_div = sum(1 for p in frames["ClassGroups"]["parent_code"] if not p)
        _standin_rooms(engine, max(3, round(n_div * 0.42)), max(3, round(n_div * 0.5)))
    rooms = [r["name"] for r in db.q(engine, "select name from rooms")]
    clean, problems = templates.parse_frames(frames, rooms)
    if not problems:
        templates.save_clean(engine, clean, "sample data")
    return problems
