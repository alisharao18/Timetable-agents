import db
import pdfs
import sample_data
import solver
import views
from test_pdfs import _text


def test_sunday_column_only_when_used(engine):
    db.set_setting(engine, "sunday_mode", "normal")
    assert sample_data.make_college(engine, 0, 0.25) == []
    p = solver.load_problem(engine)
    rows = solver.board_rows(p, solver.construct(p))
    db.replace_placements(engine, rows, "generated")
    on_sun = {p.groups[p.root(r["group_id"])]["code"] for r in rows if r["day"] == "Sun"}
    assert on_sun                                         # final/second-last years use Sunday
    code = sorted(on_sun)[0]
    assert "Sun" in views.grid(engine, "student", code).columns
    assert "Sunday" in _text(pdfs.class_pdf(engine, code))[0]
    first_year = next(v["code"] for v in p.groups.values() if v["year"] == 1 and not v["parent"]
                      and v["programme"] == "B.Tech")
    assert "Sun" not in views.grid(engine, "student", first_year).columns
    assert "Sunday" not in _text(pdfs.class_pdf(engine, first_year))[0]
