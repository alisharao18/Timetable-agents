import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
DATA = Path(__file__).parent / "data"
HAVE_WORKBOOKS = (DATA / "classrooms.xlsx").exists() and (DATA / "labs.xlsx").exists()


def pytest_collection_modifyitems(config, items):
    """The college workbooks hold staff names and emails, so they are kept out
    of git. Tests that read them are skipped when the files are not here."""
    if HAVE_WORKBOOKS:
        return
    skip = pytest.mark.skip(reason="copy the classroom and lab workbooks into "
                                   "tests/data/ as classrooms.xlsx and labs.xlsx")
    for item in items:
        if item.module.__name__ == "test_importer" or item.name == "test_reupload_diff":
            item.add_marker(skip)


@pytest.fixture
def engine(tmp_path):
    import db
    e = db.get_engine(f"sqlite:///{tmp_path/'t.db'}")
    db.init_db(e)
    return e
