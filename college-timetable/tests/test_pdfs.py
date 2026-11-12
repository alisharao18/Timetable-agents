import pytest

import db
import pdfs
import sample_data
import solver


def test_initials_follow_the_college_style():
    assert pdfs.subject_initial("Applied Time Series Analysis") == "ATSA"
    assert pdfs.subject_initial("Foundations of Machine Learning Lab") == "FOML"
    assert pdfs.subject_initial("Thermodynamics") == "THER"
    f = pdfs.faculty_initials(["Dr. Vinita Mane", "Prof. Soni Bhambar", "Dr. Siba Panda",
                               "Prof. Saurabh Pandit"])
    assert f["Dr. Vinita Mane"] == "VMA" and f["Prof. Soni Bhambar"] == "SBH"
    assert len(set(f.values())) == 4                       # never two teachers with one code
    assert pdfs._short_room("E.CR.509") == "CR-509" and pdfs._short_room("AI Lab") == "AI Lab"


@pytest.fixture(scope="module")
def published(tmp_path_factory):
    e = db.get_engine(f"sqlite:///{tmp_path_factory.mktemp('pdf') / 'p.db'}")
    db.init_db(e)
    assert sample_data.make_college(e, 0, 0.25) == []
    p = solver.load_problem(e)
    db.replace_placements(e, solver.board_rows(p, solver.construct(p)), "generated")
    return e


def _text(pdf):
    import pdfplumber, io
    with pdfplumber.open(io.BytesIO(pdf)) as doc:
        return [pg.extract_text() or "" for pg in doc.pages]


def test_class_pdf_has_header_grid_and_legends(published):
    pages = _text(pdfs.class_pdf(published, "9BTI DS"))
    assert len(pages) == 1
    t = pages[0]
    for want in ("SVKM's NMIMS", "Mukesh Patel School", "A.Y. 2026-27", "Sem IX", "Monday", "Saturday",
                 "Subject Name", "Faculty Name", "ATSA", "Dr. Siba Panda", "HOD", "Dean", "Break"):
        assert want in t, want


def test_teacher_pdf_and_all_classes(published):
    t = _text(pdfs.teacher_pdf(published, "Dr. Siba Panda"))[0]
    assert "Faculty: Dr. Siba Panda" in t and "9BTI DS" in t
    pages = _text(pdfs.all_pdf(published, "student"))
    divisions = db.q(published, "select count(*) n from class_groups where parent_id is null")[0]["n"]
    assert len(pages) >= divisions
    assert any("OE " in pg for pg in pages)                # open electives show on class sheets


def test_empty_timetable_still_makes_a_pdf(engine):
    assert pdfs.all_pdf(engine, "teacher")[:4] == b"%PDF"
