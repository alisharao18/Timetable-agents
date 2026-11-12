import pytest

import db
import sample_data
import semester
import solver


def test_defaults_and_save(engine):
    s = semester.get(engine)
    assert s["academic_year"] == "2026-27" and s["term"] == "Odd"
    semester.save(engine, academic_year="2027-28", term="Even", wef="2028-01-05")
    s = semester.get(engine)
    assert (s["academic_year"], s["term"], s["wef"]) == ("2027-28", "Even", "2028-01-05")
    assert semester.sem_number(s, 3) == 6 and semester.calendar_year(s) == 2028
    with pytest.raises(ValueError):
        semester.save(engine, term="Summer")
    with pytest.raises(ValueError):
        semester.save(engine, academic_year="2027")


def test_new_semester_archives_then_clears(engine):
    assert sample_data.make_college(engine, 0, 0.12) == []
    p = solver.load_problem(engine)
    rows = solver.board_rows(p, solver.construct(p))
    db.replace_placements(engine, rows, "generated")
    msg = semester.start_new(engine, "2026-27", "Even", "2027-01-04", "test")
    assert "archived" in msg
    assert db.q(engine, "select count(*) n from placements where source='generated'")[0]["n"] == 0
    arch = semester.archives(engine)
    assert len(arch) == 1 and arch.iloc[0]["label"] == "A.Y. 2026-27, Odd semester"
    import io, pandas as pd
    back = pd.read_excel(io.BytesIO(semester.archive_excel(engine, int(arch.iloc[0]["id"]))), sheet_name="Timetable")
    assert len(back) == len(rows)
    assert semester.get(engine)["term"] == "Even"
