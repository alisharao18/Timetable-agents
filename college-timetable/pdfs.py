"""Printable timetables in the college's own layout: one A4 landscape page
per class or per teacher, with the NMIMS / MPSTME header, a Monday-Saturday
by hour grid (2-hour labs merged), the lunch break, a subject legend, a
faculty legend, and HOD and Dean signature lines.
"""
import io
import re

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (PageBreak, Paragraph, SimpleDocTemplate, Spacer,
                                Table, TableStyle)

import db
import semester
import views
from constants import DAYS, LUNCH_HOURS, TT_HOURS

DAY_NAMES = {"Mon": "Monday", "Tue": "Tuesday", "Wed": "Wednesday", "Thu": "Thursday",
             "Fri": "Friday", "Sat": "Saturday"}
SMALL_WORDS = {"and", "to", "in", "the", "for", "with", "a", "an", "on", "&"}   # "of" stays: FOML
TITLES = re.compile(r"^(prof|dr|mr|mrs|ms|shri|smt)\.?$", re.I)

_cell = ParagraphStyle("cell", fontName="Helvetica", fontSize=7.2, leading=8.6, alignment=TA_CENTER)
_head = ParagraphStyle("head", fontName="Helvetica-Bold", fontSize=8, leading=10, alignment=TA_CENTER)
_leg = ParagraphStyle("leg", fontName="Helvetica", fontSize=7, leading=8.4)
_title = ParagraphStyle("title", fontName="Helvetica-Bold", fontSize=12, leading=15, alignment=TA_CENTER)
_sub = ParagraphStyle("sub", fontName="Helvetica", fontSize=10, leading=13, alignment=TA_CENTER)


# ------------------------------------------------------------------ initials
def subject_base(name: str) -> str:
    """'Foundations of ML Lab' -> 'Foundations of ML': a lab or practical
    shares its theory subject's entry and initial."""
    base = re.sub(r"\s*\(.*?\)\s*", " ", name)
    base = re.sub(r"\b(lab|laboratory|practical|theory)\b", "", base, flags=re.I)
    return re.sub(r"\s+", " ", base).strip() or name


def subject_initial(name: str) -> str:
    """'Applied Time Series Analysis' -> 'ATSA'."""
    base = subject_base(name)
    words = [w for w in re.split(r"[\s/\-]+", base) if w and w.lower() not in SMALL_WORDS]
    if not words:
        return name[:4].upper()
    if len(words) == 1:
        return words[0][:4].upper()
    return "".join(w[0] for w in words).upper()


def faculty_initials(names) -> dict:
    """Short codes like the college uses: first letter of the first name and
    the first two of the surname ('Vinita Mane' -> 'VMA'), with fallbacks so
    no two teachers in the college share one."""
    out, used = {}, set()
    for name in sorted(names, key=lambda n: n.lower()):
        parts = [p for p in re.split(r"\s+", name.replace(".", ". ").strip()) if p and not TITLES.match(p)]
        parts = [re.sub(r"[^A-Za-z0-9]", "", p) for p in parts]
        parts = [p for p in parts if p] or [name]
        f, l = parts[0], parts[-1]
        cands = [f[:1] + l[:2], f[:2] + l[:1], f[:1] + (parts[1][:1] if len(parts) > 2 else "") + l[:1],
                 f[:1] + l[:3], f[:2] + l[:2]]
        cands = [c.upper() for c in cands if c]
        pick = next((c for c in cands if c not in used), None)
        n = 2
        while pick is None:
            pick = f"{cands[0]}{n}" if f"{cands[0]}{n}" not in used else None
            n += 1
        used.add(pick)
        out[name] = pick
    return out


def _short_room(name: str) -> str:
    """'E.CR.509' -> 'CR-509'."""
    m = re.match(r"^E\.([A-Z]{2})\.(\d+)$", name or "")
    return f"{m.group(1)}-{m.group(2)}" if m else (name or "")


# ------------------------------------------------------------------ data
def _entries(f, rows, label):
    """Turn placement rows into grid entries: (day, start, dur, key, text)."""
    out = []
    for key, g in rows.groupby("session_key"):
        start, dur = int(g["hour"].min()), len(g)
        out.append((g["day"].iloc[0], start, dur, key, label(g.iloc[0])))
    return out


def _grid(entries, lunch_days=None):
    """-> table data (with Paragraphs) and the merge spans."""
    cells = {(d, h): [] for d in DAYS for h in TT_HOURS}
    for d, start, dur, key, text in entries:
        for k in range(dur):
            if (d, start + k) in cells:
                cells[d, start + k].append((key, start, dur, text))
    data = [[Paragraph("Time", _head)] + [Paragraph(DAY_NAMES[d], _head) for d in DAYS]]
    spans, skip = [], set()
    for i, h in enumerate(TT_HOURS, start=1):
        row = [Paragraph(f"{(h - 1) % 12 + 1}:00 - {h % 12 + 1}:00", _cell)]
        for j, d in enumerate(DAYS, start=1):
            here = cells[d, h]
            if (d, h) in skip:
                row.append("")
                continue
            if here:
                text = "<br/>".join(dict.fromkeys(t for _, _, _, t in sorted(here, key=lambda x: x[3])))
                row.append(Paragraph(text, _cell))
                nxt = cells.get((d, h + 1), [])
                if all(du == 2 and st == h for _, st, du, _ in here) and \
                        sorted(k for k, *_ in nxt) == sorted(k for k, *_ in here):
                    spans.append(((j, i), (j, i + 1)))
                    skip.add((d, h + 1))
            elif lunch_days and lunch_days.get(d) == h:
                row.append(Paragraph("Break", _cell))
            else:
                row.append("")
        data.append(row)
    return data, spans


def _lunch(entries):
    """The first free hour in the lunch window on each day with classes."""
    busy = {}
    for d, start, dur, *_ in entries:
        busy.setdefault(d, set()).update(range(start, start + dur))
    return {d: next((h for h in LUNCH_HOURS if h not in hrs), None) for d, hrs in busy.items()}


def _page(story, sem, line1, line2, entries, subjects, faculty):
    story += [Paragraph("SVKM's NMIMS", _title),
              Paragraph("Mukesh Patel School of Technology Management &amp; Engineering", _sub),
              Paragraph(f"[{sem['campus']} Campus] A.Y. {sem['academic_year']}", _sub), Spacer(1, 4)]
    info = Table([[Paragraph(f"<b>{line1}</b>", _leg), Paragraph(f"<b>{line2}</b>", _leg),
                   Paragraph(f"<b>w.e.f. {sem['wef'] or '-'}</b>", _leg)]],
                 colWidths=[13 * cm, 7.5 * cm, 6.2 * cm])
    info.setStyle(TableStyle([("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
    story.append(info)
    data, spans = _grid(entries, _lunch(entries))
    grid = Table(data, colWidths=[2.3 * cm] + [4.07 * cm] * 6,
                 rowHeights=[0.55 * cm] + [0.93 * cm] * len(TT_HOURS), repeatRows=1)
    style = [("GRID", (0, 0), (-1, -1), 0.6, colors.black),
             ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8ECF3")),
             ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("ALIGN", (0, 0), (-1, -1), "CENTER"),
             ("LEFTPADDING", (0, 0), (-1, -1), 2), ("RIGHTPADDING", (0, 0), (-1, -1), 2),
             ("TOPPADDING", (0, 0), (-1, -1), 1), ("BOTTOMPADDING", (0, 0), (-1, -1), 1)]
    for a, b in spans:
        style += [("SPAN", a, b), ("BACKGROUND", a, b, colors.HexColor("#F2F2F2"))]
    grid.setStyle(TableStyle(style))
    story += [grid, Spacer(1, 6)]

    def legend(head, pairs):
        rows = [[Paragraph(f"<b>{head[0]}</b>", _leg), Paragraph(f"<b>{head[1]}</b>", _leg)]] + \
               [[Paragraph(a, _leg), Paragraph(b, _leg)] for a, b in pairs]
        t = Table(rows, colWidths=[8.6 * cm, 2.6 * cm])
        t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.black),
                               ("TOPPADDING", (0, 0), (-1, -1), 1), ("BOTTOMPADDING", (0, 0), (-1, -1), 1)]))
        return t
    legends = Table([[legend(("Subject Name", "Initial"), subjects),
                      legend(("Faculty Name", "Initial"), faculty) if faculty else ""]],
                    colWidths=[13 * cm, 13.7 * cm])
    legends.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    sign = Table([["", "", ""], [Paragraph("HOD", _sub), "", Paragraph("Dean", _sub)]],
                 colWidths=[6 * cm, 14.7 * cm, 6 * cm], rowHeights=[0.7 * cm, 0.5 * cm])
    sign.setStyle(TableStyle([("LINEABOVE", (0, 1), (0, 1), 0.6, colors.black),
                              ("LINEABOVE", (2, 1), (2, 1), 0.6, colors.black)]))
    story += [legends, sign]


def _doc(build):
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), leftMargin=1.2 * cm, rightMargin=1.2 * cm,
                            topMargin=0.8 * cm, bottomMargin=0.7 * cm,
                            title="MPSTME timetable", author="MPSTME Timetable Committee")
    story = []
    build(story)
    if not story:
        story.append(Paragraph("Nothing to show yet: no timetable has been published.", _sub))
    doc.build(story)
    return buf.getvalue()


class _Context:
    """Everything a page needs, read once."""

    def __init__(self, engine):
        self.f = views.frame(engine)
        self.sem = semester.get(engine)
        self.groups = {r["code"]: r for r in db.q(engine, """
            select g.code, g.programme, g.year, d.name as department, p.code as parent
            from class_groups g left join departments d on d.id=g.department_id
            left join class_groups p on p.id=g.parent_id""")}
        self.children = {}
        for c, r in self.groups.items():
            if r["parent"]:
                self.children.setdefault(r["parent"], []).append(c)
        self.members = views.block_members(engine)
        self.fac = faculty_initials(self.f["teacher"].dropna().unique())
        self.subj = {}

    def subject_code(self, name):
        """Initial for a subject, unique across the college (Data Science and
        Digital Signals would both be DS: the second becomes DS2)."""
        base = subject_base(name)
        if base not in self.subj:
            code, n = subject_initial(base), 2
            first = code
            while code in self.subj.values():
                code, n = f"{first}{n}", n + 1
            self.subj[base] = code
        return self.subj[base]


def _class_page(ctx, story, code):
    f, g = ctx.f, ctx.groups.get(code)
    if g is None:
        return
    fam = {code} | set(ctx.children.get(code, [])) | ({g["parent"]} if g["parent"] else set())
    plain = f[f["class_group"].isin(fam) & f["block"].isna()]
    blocks = [b for b, codes in ctx.members.items() if codes & fam]
    def label(r):
        si, fi = ctx.subject_code(r["subject"]), ctx.fac.get(r["teacher"], "")
        batch = r["class_group"].split("-")[-1] if r["class_group"] != code and "-" in r["class_group"] else ""
        return f"{si}({fi}){' ' + batch if batch else ''}<br/>{_short_room(r['room'])}"
    entries = _entries(f, plain, label)
    for b in blocks:
        first = f[f["block"] == b].drop_duplicates(["day", "hour"])
        for (d,), grp in first.groupby(["day"]):
            hrs = sorted(grp["hour"])
            run = [hrs[0]]
            for h in hrs[1:] + [None]:
                if h is not None and h == run[-1] + 1:
                    run.append(h)
                    continue
                entries.append((d, run[0], len(run), f"{b}@{d}{run[0]}", f"<b>{b}</b>"))
                if h is not None:
                    run = [h]
    num = semester.sem_number(ctx.sem, g["year"] or 1)
    subjects = sorted({(subject_base(s), ctx.subject_code(s)) for s in plain["subject"].dropna()},
                      key=lambda x: x[1])
    subjects += [(f"{b} (elective block)", "") for b in blocks]
    faculty = sorted({(t, ctx.fac.get(t, "")) for t in plain["teacher"].dropna()}, key=lambda x: x[0])
    course = f"Course: {g['programme'] or ''} {g['department'] or ''} ({code})".replace("  ", " ")
    _page(story, ctx.sem, course, f"Sem {semester.roman(num)}", entries, subjects, faculty)


def _teacher_page(ctx, story, name):
    f = ctx.f
    rows = f[f["teacher"] == name]
    if rows.empty:
        return

    def label(r):
        si = ctx.subject_code(r["subject"])
        tag = f" [{r['block']}]" if isinstance(r.get("block"), str) else ""
        return f"{si} {r['class_group']}{tag}<br/>{_short_room(r['room'])}"
    entries = _entries(f, rows, label)
    subjects = sorted({(subject_base(s), ctx.subject_code(s)) for s in rows["subject"].dropna()},
                      key=lambda x: x[1])
    dept = rows["department"].dropna()
    hours = len(rows)
    _page(story, ctx.sem, f"Faculty: {name} ({ctx.fac.get(name, '')})",
          f"{dept.iloc[0] if len(dept) else ''} · {hours} hours a week", entries, subjects, [])


# ------------------------------------------------------------------ public
def class_pdf(engine, code: str) -> bytes:
    ctx = _Context(engine)
    return _doc(lambda story: _class_page(ctx, story, code))


def teacher_pdf(engine, name: str) -> bytes:
    ctx = _Context(engine)
    return _doc(lambda story: _teacher_page(ctx, story, name))


def all_pdf(engine, by: str) -> bytes:
    """Every division (by='student') or every teacher (by='teacher'), one page each."""
    ctx = _Context(engine)

    def build(story):
        if by == "student":
            keys = sorted(c for c, r in ctx.groups.items() if not r["parent"])
            page = _class_page
        else:
            keys = sorted(ctx.f["teacher"].dropna().unique())
            page = _teacher_page
        for k in keys:
            before = len(story)
            page(ctx, story, k)
            if len(story) > before:
                story.append(PageBreak())
        if story and isinstance(story[-1], PageBreak):
            story.pop()
    return _doc(build)
