"""MPSTME timetable and room booking: the Streamlit app.

Run:  streamlit run app.py     (from inside the college-timetable folder)
"""
import os
from datetime import date, timedelta

import pandas as pd
import streamlit as st

st.set_page_config(page_title="MPSTME Timetable", page_icon="🗓️", layout="wide")

try:                                                      # hosted: secrets -> environment
    for _k in ("DATABASE_URL", "ADMIN_EMAIL", "ADMIN_PASSWORD"):
        if _k in st.secrets and not os.environ.get(_k):
            os.environ[_k] = str(st.secrets[_k])
except Exception:
    pass

import agents
import auth
import db
import editor
import finder
import importer
import pdfs
import sample_data
import semester
import solver
import sync
import templates
import validate
import views
from constants import BOOK_HOURS, DAYS, ROLES, hour_label
from theme import grid_html, header, inject_theme

inject_theme()


@st.cache_resource
def get_engine():
    e = db.get_engine()
    db.init_db(e)
    return e


engine = get_engine()
S = st.session_state
HOSTED = bool(os.environ.get("DATABASE_URL"))


def flash(msg, kind="success"):
    S["_flash"] = (kind, msg)
    st.rerun()


if "_flash" in S:
    _kind, _msg = S.pop("_flash")
    getattr(st, _kind)(_msg)


# ------------------------------------------------------------------ sign in
def sign_in_page():
    header("MPSTME Timetable and Room Booking",
           "Mukesh Patel School of Technology Management and Engineering")
    note = auth.ensure_admin(engine)
    no_users = not db.q(engine, "select 1 from users limit 1")
    left, _ = st.columns([1, 1])
    with left:
        if no_users and not HOSTED:
            st.info("First run on this computer: create the admin account.")
            with st.form("first_admin"):
                email = st.text_input("Admin email")
                pw = st.text_input("Password (8 or more characters)", type="password")
                if st.form_submit_button("Create admin account", type="primary"):
                    ok, msg = auth.create_user(engine, email, pw, "admin")
                    if ok:
                        flash(msg + " Now sign in.")
                    st.error(msg)
            return
        if note and no_users:
            st.error(note)
            return
        with st.form("login"):
            st.subheader("Sign in")
            email = st.text_input("Email")
            pw = st.text_input("Password", type="password")
            if st.form_submit_button("Sign in", type="primary"):
                user = auth.check_login(engine, email, pw)
                if user:
                    S.user = user
                    db.log(engine, user["email"], "login")
                    st.rerun()
                st.error("Email or password is not correct.")
        st.caption("Accounts are created by the timetable committee.")


if "user" not in S:
    sign_in_page()
    st.stop()

user = S.user
role = user["role"]
inbox = sync.process_inbox(engine)                        # apply any waiting sheet edits

PAGES = ["Find a room", "Timetables", "Bookings"]
if auth.can(role, "import"):
    PAGES += ["Data", "Generate timetable", "Edit timetable", "Users"]
with st.sidebar:
    st.markdown("### MPSTME Timetable")
    st.caption(semester.label(semester.get(engine)))
    page = st.radio("Go to", PAGES, label_visibility="collapsed")
    st.divider()
    st.caption(f"Signed in as\n\n**{user['email']}**\n\n{role.title()}"
               + (f" · {user['department']}" if user.get("department") else ""))
    if st.button("Sign out"):
        del S["user"]
        st.rerun()

dept_names = [d["name"] for d in db.q(engine, "select name from departments order by name")]
n_rooms = db.q(engine, "select count(*) n from rooms")[0]["n"]


def need_rooms():
    if n_rooms == 0:
        st.info("No rooms are loaded yet. An admin needs to import the classroom and lab "
                "workbooks on the Data page.")
        return True
    return False


# ------------------------------------------------------------------ find a room
def page_find():
    header("Find a room", "Enter what you need. The best free room comes first, with alternatives.")
    if need_rooms():
        return
    lab_types = sorted({r["lab_type"] for r in db.q(engine, "select distinct lab_type from rooms where kind='lab'")
                        if r["lab_type"]})
    c1, c2, c3, c4 = st.columns(4)
    on = c1.date_input("Date", value=S.get("f_date", date.today()), min_value=date.today() - timedelta(days=1))
    start = c2.selectbox("Start time", BOOK_HOURS, index=2, format_func=lambda h: hour_label(h).split("-")[0]
                         + (" AM" if h < 12 else " PM"))
    duration = c3.number_input("Hours", min_value=1, max_value=6, value=1)
    seats = c4.number_input("Seats needed", min_value=1, max_value=500, value=40)
    c5, c6, c7 = st.columns(3)
    kind = c5.radio("Room", ["Classroom", "Lab"], horizontal=True)
    if kind == "Classroom":
        rt = c6.selectbox("Classroom type", ["Any", "STEP", "FLAT"])
        need = dict(kind="classroom", room_type=None if rt == "Any" else rt)
    else:
        lt = c6.selectbox("Lab type", ["Any"] + lab_types)
        need = dict(kind="lab", lab_type=None if lt == "Any" else lt)
    opts = ["(none)"] + dept_names
    default = opts.index(user["department"]) if user.get("department") in opts else 0
    dept = c7.selectbox("Your department", opts, index=default,
                        help="Rooms assigned to your department are offered first.")
    dept = None if dept == "(none)" else dept
    need.update(seats=int(seats), department=dept)

    bad = finder.check_request(on, int(start), int(duration))
    if bad:
        st.warning(bad)
        return
    rooms = finder.find_rooms(engine, on, int(start), int(duration), **need)
    when = f"{on:%A %d %b %Y}, {start}:00 to {int(start) + int(duration)}:00"
    if not rooms:
        st.error(f"No {kind.lower()} with {int(seats)}+ seats is free on {when}.")
        alt = finder.nearest_free(engine, on, int(start), int(duration), **need)
        if alt:
            st.markdown("**Nearest free times for the same need**")
            st.dataframe(pd.DataFrame([{"Date": f"{a['date']:%a %d %b}",
                                        "Time": f"{a['start_hour']}:00 to {a['start_hour'] + int(duration)}:00",
                                        "Best room": a["room"], "Rooms free": a["rooms_free"]} for a in alt]),
                         width="stretch", hide_index=True)
        else:
            st.caption("Nothing suitable is free in the next week either. Try fewer seats or a shorter time.")
        return

    best = rooms[0]
    tag = '<span class="tt-tag">your department</span>' if best["own_department"] else ""
    st.markdown(
        f'<div class="tt-pick"><span>Best match for {when}</span><br><b>{best["room"]}</b>{tag}<br>'
        f'<span>{best["capacity"]} seats · {best["room_type"] or ""}'
        f'{" · " + best["lab_type"] if best["lab_type"] else ""} · floor {best["floor"]}'
        f'{" · usually: " + best["owner"] if best["owner"] else ""}</span></div>', unsafe_allow_html=True)
    st.markdown(f"**All {len(rooms)} free rooms that fit**")
    st.dataframe(pd.DataFrame([{
        "Room": r["room"], "Seats": r["capacity"], "Spare seats": r["spare_seats"],
        "Type": r["lab_type"] or r["room_type"], "Floor": r["floor"],
        "Your dept": "yes" if r["own_department"] else "", "Usually used by": r["owner"]} for r in rooms]),
        width="stretch", hide_index=True, height=min(420, 40 + 35 * len(rooms)))

    if not auth.can(role, "book"):
        st.caption("Your account can view availability but not book. Ask the committee for coordinator access.")
        return
    with st.form("book"):
        st.subheader("Book it")
        b1, b2 = st.columns([1, 2])
        pick = b1.selectbox("Room", [r["room"] for r in rooms])
        purpose = b2.text_input("What is it for?", placeholder="e.g. Extra lecture, 5B IT, Prof. Rao")
        if st.form_submit_button(f"Book for {when}", type="primary"):
            ok, msg = finder.book(engine, pick, on, int(start), int(duration), dept, purpose, user["email"])
            S.f_date = on
            flash(msg, "success" if ok else "error")


# ------------------------------------------------------------------ timetables
def page_timetables():
    header("Timetables", "Four views of the same timetable: by class, teacher, department or room.")
    if need_rooms():
        return
    f = views.frame(engine)
    has_generated = bool((f["source"] == "generated").any()) if len(f) else False
    by_label = st.radio("View by", ["Class", "Teacher", "Department", "Room"], horizontal=True,
                        index=0 if has_generated else 3)
    by = {"Class": "student", "Teacher": "teacher", "Department": "department", "Room": "room"}[by_label]
    names = views.keys(engine, by, f)
    if not names:
        st.info("Class, teacher and department timetables appear once a timetable has been generated "
                "and published. Until then, the Room view shows what was imported from the sheets.")
        return
    c1, c2 = st.columns([2, 1])
    key = c1.selectbox(by_label, names)
    week = None
    if by == "room":
        week = c2.date_input("Show bookings for the week of", value=date.today())
    if by != "room" and not has_generated:
        st.caption("Showing imported data only.")
    table = views.grid(engine, by, key, week, f)
    st.markdown(grid_html(table), unsafe_allow_html=True)
    st.write("")
    d1, d2, d3 = st.columns(3)
    d1.download_button(f"Download this {by_label.lower()} (CSV)", table.to_csv(), f"{key}.csv", "text/csv")
    if d2.button(f"Prepare all {by_label.lower()} timetables (Excel)"):
        S["_xl"] = (by, views.export_excel(engine, by, week))
    if S.get("_xl", (None,))[0] == by:
        d2.download_button("Download Excel", S["_xl"][1], f"timetables_by_{by}.xlsx",
                           "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    d3.download_button("Download full list (CSV)", views.flat(engine, f).to_csv(index=False),
                       "timetable_full.csv", "text/csv")
    if by in ("student", "teacher"):
        st.markdown("**Printable sheets in the college format**")
        p1, p2 = st.columns(3)[:2]
        one = pdfs.class_pdf(engine, key) if by == "student" else pdfs.teacher_pdf(engine, key)
        p1.download_button(f"Download PDF for {key}", one, f"{key}.pdf", "application/pdf")
        if p2.button(f"Prepare all {by_label.lower()} sheets (PDF)"):
            with st.spinner("Building one page per " + by_label.lower() + "..."):
                S["_pdf"] = (by, pdfs.all_pdf(engine, by))
        if S.get("_pdf", (None,))[0] == by:
            p2.download_button("Download PDF (all)", S["_pdf"][1], f"timetables_{by}.pdf", "application/pdf")


# ------------------------------------------------------------------ bookings
def page_bookings():
    header("Bookings", "One-off room bookings. Each one applies to its date only.")
    show_all = auth.can(role, "cancel_any") or st.toggle("Show everyone's bookings", value=False)
    upcoming = st.toggle("Upcoming only", value=True)
    rows = finder.list_bookings(engine, None if show_all else user["email"],
                                date.today() if upcoming else None)
    if not rows:
        st.info("No bookings to show.")
        return
    table = pd.DataFrame([{
        "Date": pd.Timestamp(r["date"]).strftime("%a %d %b %Y"), "Room": r["room"],
        "Time": f"{r['start_hour']}:00 to {r['start_hour'] + r['hours']}:00",
        "Department": r["department"] or "", "Purpose": r["purpose"] or "",
        "Booked by": r["booked_by"] or ""} for r in rows])
    st.dataframe(table, width="stretch", hide_index=True, height=min(520, 40 + 35 * len(table)))
    st.download_button("Download (CSV)", table.to_csv(index=False), "bookings.csv", "text/csv")
    mine = [r for r in rows if auth.can(role, "cancel_any")
            or (auth.can(role, "cancel_own") and r["booked_by"] == user["email"])]
    if mine:
        st.subheader("Cancel a booking")
        label = lambda r: (f"{pd.Timestamp(r['date']):%d %b} · {r['room']} · {r['start_hour']}:00 · "
                           f"{(r['purpose'] or '')[:40]} · {r['booked_by']}")
        pick = st.selectbox("Booking", mine, format_func=label)
        if st.button("Cancel this booking"):
            ok, msg = finder.cancel(engine, pick["booking_key"], user["email"], auth.can(role, "cancel_any"))
            flash(msg, "success" if ok else "error")


# ------------------------------------------------------------------ data (admin)
def page_data():
    header("Data", "Rooms, classes, teachers and preferences. Everything the timetable is built from.")
    counts = {t: db.q(engine, f"select count(*) n from {t}")[0]["n"]
              for t in ("rooms", "class_groups", "teachers", "teaching_load", "placements", "bookings")}
    m = st.columns(6)
    for col, (label, key) in zip(m, [("Rooms and labs", "rooms"), ("Class groups", "class_groups"),
                                     ("Teachers", "teachers"), ("Load rows", "teaching_load"),
                                     ("Weekly cells", "placements"), ("Dated bookings", "bookings")]):
        col.metric(label, counts[key])
    t1, t2, t3, t4, t5 = st.tabs(["1 · Room workbooks", "2 · Classes and teachers", "Needs checking",
                                  "Sheet edits", "Settings"])

    with t1:
        st.write("Upload the two workbooks the committee already maintains, exactly as they are.")
        for label, parser, key in (("Classroom workbook (one tab per day)", importer.parse_classrooms, "cr"),
                                   ("Lab workbook (one tab per floor)", importer.parse_labs, "lab")):
            up = st.file_uploader(label, type=["xlsx"], key=f"up_{key}")
            if up is None:
                continue
            try:
                res = parser(up)
            except Exception as err:
                st.error(f"That workbook could not be read: {err}")
                continue
            if not res.rooms:
                st.error("No rooms were found. Is this the right workbook?")
                continue
            st.success(f"Read {len(res.rooms)} rooms, {len(res.weekly)} weekly class cells, "
                       f"{len(res.dated)} dated bookings and {len(res.unresolved)} cells that need a person.")
            for n in res.notes:
                st.warning(n)
            diff = sync.diff_upload(engine, res)
            if counts["rooms"] and len(diff):
                st.markdown(f"**{len(diff)} change(s) compared with what is stored**")
                st.dataframe(diff, width="stretch", hide_index=True, height=min(300, 40 + 35 * len(diff)))
            elif counts["rooms"]:
                st.caption("The weekly grid matches what is already stored.")
            if st.button(f"Save {len(res.rooms)} rooms to the database", key=f"save_{key}", type="primary"):
                c = importer.load_into_db(engine, res, user["email"])
                flash(f"Saved {c['rooms']} rooms, {c['weekly']} weekly cells, {c['dated']} new dated bookings. "
                      f"{c['unresolved']} cells need checking." +
                      (f" {c['weekly_skipped']} cells were skipped because the published timetable already uses them."
                       if c["weekly_skipped"] else ""))
        rooms = db.df(engine, "select name, kind, room_type, lab_type, capacity, floor, owner_label from rooms order by kind, name")
        if len(rooms):
            with st.expander(f"Room list ({len(rooms)})"):
                st.dataframe(rooms, width="stretch", hide_index=True)

    with t2:
        st.write("Departments, class groups, teachers, teaching load and preferences come in one workbook "
                 "with eight tabs (Electives is optional).")
        a, b, c = st.columns(3)
        a.download_button("Download blank template", templates.build_template_workbook(),
                          "timetable_inputs_template.xlsx")
        if counts["class_groups"]:
            b.download_button("Download current data", templates.export_current(engine),
                              "timetable_inputs_current.xlsx")
        up = st.file_uploader("Upload the filled workbook", type=["xlsx"], key="up_inputs")
        if up is not None and st.button("Check and save", type="primary"):
            problems = templates.load_workbook_into_db(engine, up, user["email"])
            if problems:
                st.error(f"Nothing was saved. Fix these {len(problems)} problem(s) and upload again:")
                st.dataframe(pd.DataFrame({"Problem": problems}), width="stretch", hide_index=True)
            else:
                flash("Classes, teachers, load and preferences saved.")
        st.divider()
        st.write("No real teaching-load sheet yet? Load a made-up college of realistic size to try the generator.")
        if n_rooms == 0:
            st.caption("Import the room workbooks first so the sample uses the real rooms.")
        s1, s2 = st.columns([1, 3])
        scale = s1.select_slider("Size", options=[0.25, 0.5, 1.0], value=1.0,
                                 format_func=lambda v: {0.25: "Small", 0.5: "Medium", 1.0: "Full college"}[v])
        if s2.button("Load sample college (replaces classes, teachers and load)"):
            problems = sample_data.make_college(engine, seed=0, scale=scale)
            flash("Sample college loaded." if not problems else "; ".join(problems[:3]),
                  "success" if not problems else "error")
        if counts["class_groups"]:
            with st.expander("Department rooms (which rooms each department is authorised to use)"):
                st.dataframe(templates.read_current(engine)["DepartmentRooms"], width="stretch", hide_index=True)
                st.caption("Guessed from the labels in the room workbooks unless you upload a DepartmentRooms tab.")

    with t3:
        un = db.df(engine, """select u.id, r.name as room, u.day, u.hour, u.text from unresolved u
                              join rooms r on r.id=u.room_id order by r.name, u.day, u.hour""")
        if un.empty:
            st.success("Nothing needs checking.")
        else:
            st.write(f"{len(un)} sheet cell(s) say a room is booked but give no date the system could read. "
                     "Each stays blocked every week until you clear it here.")
            st.dataframe(un.drop(columns="id"), width="stretch", hide_index=True)
            pick = st.multiselect("Cells that are no longer needed", un["id"].tolist(),
                                  format_func=lambda i: "{room} {day} {hour}:00 — {text}".format(
                                      **un[un["id"] == i].iloc[0].to_dict())[:110])
            if pick and st.button("Unblock selected cells"):
                for i in pick:
                    row = db.q(engine, "select room_id, day, hour from unresolved where id=:i", i=int(i))[0]
                    db.run(engine, "delete from placements where room_id=:r and day=:d and hour=:h and source='unresolved'",
                           r=row["room_id"], d=row["day"], h=row["hour"])
                    db.run(engine, "delete from unresolved where id=:i", i=int(i))
                flash(f"Unblocked {len(pick)} cell(s).")

    with t4:
        st.write("Edits made on the linked Google Sheet arrive here. Clashing edits are rejected, never applied.")
        if inbox["applied"] or inbox["rejected"]:
            st.info(f"Just processed: {inbox['applied']} applied, {inbox['rejected']} rejected.")
        ed = db.df(engine, "select at, sheet, room, day, hour, new_text, editor, status, note from sheet_edits order by id desc limit 300")
        if ed.empty:
            st.caption("No sheet edits received yet. See SETUP.md to link the Google Sheet.")
        else:
            only = st.toggle("Rejected only", value=False)
            st.dataframe(ed[ed["status"] == "rejected"] if only else ed, width="stretch", hide_index=True)

    with t5:
        sem = semester.get(engine)
        st.markdown("**Semester**")
        st.caption("Used for semester numbers, the importer's year and the PDF headers.")
        with st.form("semester"):
            a1, a2, a3, a4 = st.columns(4)
            ay = a1.text_input("Academic year", sem["academic_year"], help="For example 2026-27")
            term = a2.selectbox("Term", ["Odd", "Even"], index=0 if sem["term"] == "Odd" else 1)
            wef = a3.text_input("With effect from", sem["wef"], placeholder="e.g. 20 July 2026")
            campus = a4.text_input("Campus", sem["campus"])
            if st.form_submit_button("Save semester settings"):
                try:
                    semester.save(engine, academic_year=ay, term=term, wef=wef, campus=campus)
                    flash("Semester settings saved.")
                except ValueError as err:
                    st.error(str(err))
        with st.expander("Start a new semester"):
            st.write("This archives the published timetable (you can download it below at any time), then "
                     "clears generated and hand-edited classes and all locks. Rooms, bookings, classes, "
                     "teachers and the teaching load are kept: upload the new semester's load afterwards.")
            with st.form("new_sem"):
                n1, n2, n3 = st.columns(3)
                nay = n1.text_input("New academic year", sem["academic_year"])
                nterm = n2.selectbox("New term", ["Even", "Odd"], index=0 if sem["term"] == "Odd" else 1)
                nwef = n3.text_input("With effect from", "")
                sure = st.checkbox("I understand the current timetable will be archived and cleared")
                if st.form_submit_button("Start new semester", type="primary"):
                    if not sure:
                        st.error("Tick the box to confirm.")
                    else:
                        try:
                            flash(semester.start_new(engine, nay, nterm, nwef, user["email"]))
                        except ValueError as err:
                            st.error(str(err))
        arch = semester.archives(engine)
        if len(arch):
            st.markdown("**Archived timetables**")
            pick = st.selectbox("Archive", arch["id"].tolist(),
                                format_func=lambda i: f"{arch.set_index('id').at[i, 'label']} "
                                                      f"(saved {pd.Timestamp(arch.set_index('id').at[i, 'created_at']):%d %b %Y})")
            st.download_button("Download archived timetable (Excel)", semester.archive_excel(engine, int(pick)),
                               f"archive_{pick}.xlsx",
                               "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        st.divider()
        st.markdown("**Sunday classes**")
        st.caption("Sunday is used only for classes whose days_on_campus list Sun (for example the final "
                   "and second-last years). Choose how the generator treats it for them.")
        modes = {"normal": "Like any other day", "only_when_needed": "Only when the weekdays cannot fit the class"}
        cur_mode = db.get_setting(engine, "sunday_mode", "normal")
        mode = st.radio("Sunday", list(modes), format_func=modes.get, index=list(modes).index(cur_mode),
                        horizontal=True)
        if mode != cur_mode and st.button("Save Sunday setting"):
            db.set_setting(engine, "sunday_mode", mode)
            flash("Saved. Generate again to apply it.")
        st.divider()
        cur = int(float(db.get_setting(engine, "canteen_limit", 0) or 0))
        lim = st.number_input("Canteen limit: most students who should be at lunch in the same hour (0 = not set)",
                              min_value=0, max_value=20000, value=cur, step=50)
        if st.button("Save setting") and lim != cur:
            db.set_setting(engine, "canteen_limit", int(lim))
            flash("Saved.")
        st.caption("Lunch hours are always spread across 12-1, 1-2 and 2-3. The generator reports whether "
                   "the busiest hour stays under this limit.")
        log = db.df(engine, "select at, user, action, detail from audit_log order by id desc limit 200")
        with st.expander("Activity log"):
            st.dataframe(log, width="stretch", hide_index=True)


# ------------------------------------------------------------------ generate (admin)
def _score_row(label, sc):
    return {"Stage": label, "Hours placed": f"{sc['placed_hours']}/{sc['total_hours']}",
            "Hours with no complaint": f"{sc['preference']}%", "Worst-off dept (0-10)": sc["fairness_min"],
            "In own dept rooms": "n/a" if sc["own_room_share"] is None else f"{sc['own_room_share']}%",
            "Lectures in home room": f"{sc['home_room_share']}%",
            "Busiest lunch hour": f"{sc['lunch_peak_share']}% of students",
            "Idle hours per class-day": sc["gaps"], "Penalty": sc["penalty"]}


def page_generate():
    header("Generate timetable", "Check the inputs, build the timetable, let the agents negotiate, then publish.")
    if need_rooms():
        return
    if not db.q(engine, "select 1 from teaching_load limit 1"):
        st.info("There is no teaching load yet. Upload the inputs workbook, or load the sample college, "
                "on the Data page.")
        return
    p = solver.load_problem(engine)
    hours = sum(s["dur"] for s in p.sessions)
    m = st.columns(6)
    m[0].metric("Class hours a week", hours)
    m[1].metric("Class groups", len(p.groups))
    m[2].metric("Teachers", len(p.teachers))
    m[3].metric("Rooms and labs", len(p.rooms))
    m[4].metric("Elective blocks", len({s["block"] for s in p.sessions if s.get("block")}))
    m[5].metric("Locked classes", sum(1 for s in p.sessions if s.get("lock")))
    problems = solver.check_feasible(p)
    if problems:
        st.error(f"{len(problems)} thing(s) make a complete timetable impossible. You can still generate; "
                 "the affected classes will be listed as not placed.")
        st.dataframe(pd.DataFrame({"Problem": problems}), width="stretch", hide_index=True)
    else:
        st.success("Inputs check passed: nothing obviously impossible.")

    if solver.CP_SAT_ERROR:
        st.warning("The exact solver (OR-Tools) could not be loaded on this computer, so the "
                   "polish step is skipped. Timetables are still complete and checked. "
                   "See 'OR-Tools will not load on Windows' in README.md to fix it.\n\n"
                   f"Details: {solver.CP_SAT_ERROR}")
    c1, c2, c3 = st.columns(3)
    polish = c1.slider("Exact-solver polish (seconds)", 0, 300,
                       0 if solver.CP_SAT_ERROR else 60, step=30,
                       disabled=bool(solver.CP_SAT_ERROR),
                       help="CP-SAT re-times one department-year at a time. 0 skips this step.")
    rounds = c2.slider("Negotiation rounds", 0, 400, 150, step=50,
                       help="Department and room agents trade slots and rooms. 0 skips this step.")
    algo = c3.radio("Agents learn with", ["expected_sarsa", "q_learning"],
                    format_func=lambda a: {"expected_sarsa": "Expected SARSA", "q_learning": "Q-Learning"}[a])
    if st.button("Generate", type="primary"):
        with st.status("Building the timetable...", expanded=True) as status:
            st.write("Placing every class, most constrained first...")
            b = solver.construct(p, seed=0)
            rows = solver.board_rows(p, b)
            stages = [_score_row("1 · Constructed", validate.score(p, rows))]
            if polish:
                st.write(f"Polishing with the exact solver (about {polish} seconds)...")
                solver.improve(p, b, polish, workers=max(2, min(8, os.cpu_count() or 2)))
                rows = solver.board_rows(p, b)
                stages.append(_score_row("2 · After exact-solver polish", validate.score(p, rows)))
            report = None
            if rounds:
                st.write(f"Agents negotiating for {rounds} rounds...")
                rows, report = agents.negotiate(p, rows, rounds=rounds, algo=algo, seed=0)
                stages.append(_score_row("3 · After agent negotiation", report["after"]))
            st.write("Auditing every rule independently...")
            errors = validate.audit(p, rows)
            from board import Board
            unplaced = solver.unplaced_reasons(p, Board(p).load_rows(rows))
            S.draft = dict(rows=rows, stages=stages, report=report, errors=errors, unplaced=[
                {"Class": p.groups[u["session"]["group"]]["code"], "Subject": u["session"]["subject"],
                 "Teacher": p.teachers[u["session"]["teacher"]]["name"], "Why": u["reason"]} for u in unplaced],
                score=validate.score(p, rows), lunch=validate.lunch_plan(p, rows)[0],
                students=sum(p.groups[g]["size"] for g in p.leaves()), limit=p.canteen_limit,
                lock_notes=list(p.lock_notes))
            status.update(label="Done", state="complete", expanded=False)

    d = S.get("draft")
    if not d:
        return
    st.subheader("Result (draft, not yet published)")
    for note in d.get("lock_notes", []):
        st.warning(note)
    if d["errors"]:
        st.error(f"The audit found {len(d['errors'])} rule violation(s). This is a bug; do not publish.")
        st.dataframe(pd.DataFrame({"Violation": d["errors"]}), width="stretch", hide_index=True)
    else:
        st.success("Audit passed: no double-booked rooms, teachers or classes; every class fits its room; "
                   "everyone has a lunch hour.")
    st.dataframe(pd.DataFrame(d["stages"]), width="stretch", hide_index=True)
    st.caption("Penalty is the single number the solver and agents try to lower: unmet preferences, "
               "extra days on campus and idle hours. Lower is better.")
    if d["unplaced"]:
        st.warning(f"{len(d['unplaced'])} class session(s) could not be placed:")
        st.dataframe(pd.DataFrame(d["unplaced"]), width="stretch", hide_index=True)

    lunch = pd.DataFrame([{"Day": day, **{hour_label(h): d["lunch"].get((day, h), 0) for h in (12, 13, 14)}}
                          for day in DAYS]).set_index("Day")
    st.markdown("**Students at lunch in each hour**")
    st.dataframe(lunch, width="stretch")
    peak = int(lunch.values.max()) if lunch.size else 0
    if d["limit"]:
        (st.success if peak <= d["limit"] else st.warning)(
            f"Busiest lunch hour: {peak} students. Canteen limit: {d['limit']}.")

    rep = d["report"]
    if rep:
        with st.expander("What the agents did"):
            a = st.columns(4)
            a[0].metric("Requests made", rep["proposals"])
            a[1].metric("Accepted", rep["accepted"])
            a[2].metric("Room disputes ruled on", rep["contested"])
            a[3].metric("Requests conceded", rep["conceded"])
            st.line_chart(pd.DataFrame(rep["history"], columns=["Round", "Penalty"]).set_index("Round"))
            st.markdown("**Department agents**")
            st.dataframe(pd.DataFrame([{"Department": k, "Penalty per hour before": v["before"],
                                        "After": v["after"], "Reward earned": v["reward"], **v["actions"]}
                                       for k, v in rep["per_dept"].items()]), width="stretch", hide_index=True)
            if rep["room_rules"]:
                st.markdown("**Room agents: how often each floor used each rule**")
                st.dataframe(pd.DataFrame(rep["room_rules"]).T, width="stretch")

    st.divider()
    st.write("Publishing makes this the live timetable. It replaces the weekly classes imported from the "
             "sheets and any earlier generated timetable. Dated bookings are kept.")
    ok = st.checkbox("I have reviewed the result")
    if st.button("Publish timetable", type="primary", disabled=bool(d["errors"]) or not ok):
        try:
            db.replace_placements(engine, d["rows"], "generated", ["generated", "sheet", "manual"])
        except Exception as err:
            st.error(f"Could not publish: a room cell was taken in the meantime ({err.__class__.__name__}). "
                     "Generate again.")
            return
        db.log(engine, user["email"], "publish", f"{len(d['rows'])} cells, penalty {d['score']['penalty']}")
        del S["draft"]
        flash("Timetable published. See it under Timetables.")


# ------------------------------------------------------------------ edit (admin)
def page_edit():
    header("Edit timetable", "Move or swap a class by hand, and lock classes so regenerating keeps them.")
    if not db.q(engine, "select 1 from placements where session_key is not null limit 1"):
        st.info("Publish a timetable first (Generate timetable page).")
        return
    p, b = editor.current(engine)
    c1, c2 = st.columns([1, 3])
    by = c1.radio("Find classes by", ["Class", "Teacher"], horizontal=True)
    if by == "Class":
        names = sorted(v["code"] for v in p.groups.values())
        key = c2.selectbox("Class", names)
        items = editor.sessions(p, b, group_code=key)
    else:
        names = sorted(v["name"] for v in p.teachers.values())
        key = c2.selectbox("Teacher", names)
        items = editor.sessions(p, b, teacher=key)
    if not items:
        st.info("No classes for this selection.")
        return
    labels = {i["key"]: i["text"] for i in items}
    sel = st.selectbox("Class session", list(labels), format_func=labels.get)
    item = next(i for i in items if i["key"] == sel)

    tab_move, tab_swap, tab_lock = st.tabs(["Move", "Swap", "Lock"])
    with tab_move:
        if not item["movable"]:
            st.info("This is part of an elective block: all its options run together, so it cannot be moved "
                    "alone. Change the Electives tab and generate again.")
        elif item["locked"]:
            st.info("This class is locked. Unlock it on the Lock tab to move it.")
        else:
            opts = [o for o in editor.options(p, b, sel) if not o["current"]]
            if not opts:
                st.warning("There is no other time this class could go without breaking a rule.")
            else:
                st.caption(f"{len(opts)} legal time(s). Every one keeps the students, the teacher and the room "
                           "clash-free, keeps a lunch hour and respects campus days and must-have limits.")
                o_lab = {i: f"{o['day']} {hour_label(o['hour'])} · {len(o['rooms'])} free room(s)"
                         for i, o in enumerate(opts)}
                oi = st.selectbox("New time", list(o_lab), format_func=o_lab.get)
                room = st.selectbox("Room", opts[oi]["rooms"],
                                    help="Rooms of this class's department come first, then smallest that fits.")
                if st.button("Move here", type="primary"):
                    ok, msg = editor.move(engine, sel, opts[oi]["day"], opts[oi]["hour"], room, user["email"])
                    flash(msg, "success" if ok else "error")
    with tab_swap:
        others = {i["key"]: i["text"] for i in items if i["key"] != sel and i["movable"] and not i["locked"]}
        if not item["movable"] or item["locked"] or not others:
            st.info("Nothing to swap with here.")
        else:
            other = st.selectbox("Swap times with", list(others), format_func=others.get)
            if st.button("Swap"):
                ok, msg = editor.swap(engine, sel, other, user["email"])
                flash(msg, "success" if ok else "error")
    with tab_lock:
        st.caption("A locked class keeps its day, time and room when the timetable is generated again.")
        l1, l2, l3 = st.columns(3)
        if item["locked"]:
            if l1.button("Unlock this class"):
                flash(editor.unlock(engine, [sel], user["email"])[1])
        elif item["movable"]:
            if l1.button("Lock this class"):
                ok, msg = editor.lock(engine, [sel], user["email"])
                flash(msg, "success" if ok else "error")
        if l2.button(f"Lock everything for {key}"):
            ok, msg = editor.lock(engine, [i["key"] for i in items], user["email"])
            flash(msg, "success" if ok else "error")
        if l3.button(f"Unlock everything for {key}"):
            flash(editor.unlock(engine, [i["key"] for i in items], user["email"])[1])
        locks = editor.all_locks(engine)
        st.markdown(f"**All locked classes ({len(locks)})**")
        if len(locks):
            locks["Hour"] = locks["Hour"].map(hour_label)
            st.dataframe(locks, width="stretch", hide_index=True)


# ------------------------------------------------------------------ users (admin)
def page_users():
    header("Users", "Who can sign in, and what they can do.")
    users = db.df(engine, "select email, role, department from users order by role, email")
    st.dataframe(users, width="stretch", hide_index=True)
    st.caption("Admin: everything. Coordinator: find and book rooms. Viewer: view only.")
    with st.form("add_user", clear_on_submit=True):
        st.subheader("Add a user")
        c1, c2, c3, c4 = st.columns(4)
        email = c1.text_input("Email")
        pw = c2.text_input("Temporary password", type="password")
        r = c3.selectbox("Role", ROLES, index=1)
        dp = c4.selectbox("Department", ["(none)"] + dept_names)
        if st.form_submit_button("Add user", type="primary"):
            ok, msg = auth.create_user(engine, email, pw, r, None if dp == "(none)" else dp)
            if ok:
                db.log(engine, user["email"], "add_user", f"{email} {r}")
                flash(msg)
            st.error(msg)
    others = [u for u in users["email"] if u != user["email"]]
    if others:
        c1, c2 = st.columns(2)
        who = c1.selectbox("Remove a user", others)
        if c1.button("Remove"):
            ok, msg = auth.remove_user(engine, who)
            flash(msg, "success" if ok else "error")
    with st.form("my_pw", clear_on_submit=True):
        st.subheader("Change my password")
        new = st.text_input("New password", type="password")
        if st.form_submit_button("Change password"):
            ok, msg = auth.set_password(engine, user["email"], new)
            (st.success if ok else st.error)(msg)


{"Find a room": page_find, "Timetables": page_timetables, "Bookings": page_bookings,
 "Data": page_data, "Generate timetable": page_generate, "Edit timetable": page_edit,
 "Users": page_users}[page]()
