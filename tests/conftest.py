import os
import tempfile

_tmp = tempfile.mkdtemp()
os.environ["FINSIGHT_DB"] = os.path.join(_tmp, "test.db")
os.environ["FINSIGHT_DISABLE_LLM"] = "1"

import pytest  # noqa: E402

from app import analytics, db, ingest  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def seeded_db():
    db.seed_if_empty(ingest.CSV_PATH)


@pytest.fixture(scope="session")
def df(seeded_db):
    return analytics.get_frame()
