import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
DATA = Path(__file__).parent / "data"


@pytest.fixture
def engine(tmp_path):
    import db
    e = db.get_engine(f"sqlite:///{tmp_path/'t.db'}")
    db.init_db(e)
    return e
