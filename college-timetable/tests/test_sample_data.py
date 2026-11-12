import pytest

import sample_data
import solver
import validate

EXPECTED = {("B.Tech", y) for y in (1, 2, 3, 4)} | {("MBA Tech", y) for y in (1, 2, 3, 4, 5)} \
    | {("BTI", y) for y in (4, 5, 6)}


@pytest.mark.parametrize("scale", [0.12, 0.25, 0.5, 1.0])
def test_every_programme_runs_all_its_years(engine, scale):
    g = sample_data.make_frames(engine, 0, scale)["ClassGroups"]
    divs = g[g["parent_code"] == ""]
    have = set(zip(divs["programme"], divs["year"].astype(int)))
    assert EXPECTED <= have
    assert not any(p == "BTI" and y < 4 for p, y in have)          # BTI admissions have stopped
    if scale >= 0.25:
        assert {("MCA", 1), ("MCA", 2)} <= have


def test_real_bti_ds_class_is_included_with_a_3_day_must(engine):
    f = sample_data.make_frames(engine, 0, 0.12)
    load = f["TeachingLoad"]
    mine = load[load["class_group"].str.startswith(sample_data.REAL_CODE)]
    assert len(mine) == len(sample_data.REAL_LOAD)
    assert "Dr. Siba Panda" in set(mine["teacher"])
    pref = f["ClassPreferences"].set_index("class_group").loc[sample_data.REAL_CODE]
    assert int(pref["max_days"]) == 3 and pref["must"] == "yes"


def test_real_class_gets_at_most_3_days_in_a_generated_timetable(engine):
    assert sample_data.make_college(engine, seed=0, scale=0.12) == []
    p = solver.load_problem(engine)
    assert solver.check_feasible(p) == []
    b = solver.construct(p)
    assert len(b.time) == len(p.sessions)
    rows = solver.board_rows(p, b)
    assert validate.audit(p, rows) == []
    real = {g for g, v in p.groups.items() if v["code"].startswith(sample_data.REAL_CODE)}
    for leaf in real:
        if p.groups[leaf]["parent"]:
            days = {r["day"] for r in rows if r["group_id"] in (leaf, p.groups[leaf]["parent"])}
            assert 1 <= len(days) <= 3


def test_full_college_has_other_programmes_and_electives(engine):
    f = sample_data.make_frames(engine, 0, 1.0)
    g = f["ClassGroups"]
    progs = set(g[g["parent_code"] == ""]["programme"])
    assert {"Diploma", "M.Tech", "PhD"} <= progs
    el = f["Electives"]
    oe = el[el["block"].str.startswith("OE ")]
    assert set(oe["block"]) >= {"OE V B.Tech", "OE VII B.Tech"}
    assert all(len(c.split(",")) > 1 for c in oe["class_groups"])      # spans many divisions
    assert (el["block"].str.startswith("DE ")).any()


def test_even_term_numbers_semesters_and_the_real_class(engine):
    import semester
    semester.save(engine, term="Even")
    g = sample_data.make_frames(engine, 0, 0.12)["ClassGroups"]
    codes = set(g["code"])
    assert "10BTI DS" in codes and "9BTI DS" not in codes
    assert any(c.startswith("2B ") for c in codes) and not any(c.startswith("1B ") for c in codes)


def test_only_final_and_second_last_years_may_come_on_sunday(engine):
    f = sample_data.make_frames(engine, 0, 1.0)
    g = f["ClassGroups"].set_index("code")
    prefs = f["ClassPreferences"].set_index("class_group")
    for code, row in prefs.iterrows():
        prog, year = g.at[code, "programme"], int(g.at[code, "year"])
        has_sun = "Sun" in str(row["days_on_campus"])
        assert has_sun == sample_data.sunday_ok(prog, year), (code, prog, year)
    assert sample_data.sunday_ok("B.Tech", 4) and sample_data.sunday_ok("B.Tech", 3)
    assert not sample_data.sunday_ok("B.Tech", 2) and not sample_data.sunday_ok("PhD", 1)
