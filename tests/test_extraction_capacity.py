import subprocess
import sys

import pytest
from fastapi import HTTPException

from app.extraction_capacity import extraction_slot


def test_rejects_overlap_and_releases_on_error(tmp_path):
    path = str(tmp_path / "extraction.lock")
    with pytest.raises(RuntimeError):
        with extraction_slot(path):
            with pytest.raises(HTTPException) as busy:
                with extraction_slot(path):
                    pytest.fail("Overlapping work admitted")
            assert busy.value.status_code == 503
            assert busy.value.headers == {"Retry-After": "30"}
            raise RuntimeError("Processing failed")
    with extraction_slot(path):
        pass


def test_lock_shared_across_processes_and_released_on_worker_death(tmp_path):
    path = str(tmp_path / "extraction.lock")
    worker = subprocess.Popen(
        [sys.executable, "-c", """
import sys
from app.extraction_capacity import extraction_slot
with extraction_slot(sys.argv[1]):
    print('ready', flush=True)
    sys.stdin.read()
""", path],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert worker.stdout.readline().strip() == "ready"
        with pytest.raises(HTTPException):
            with extraction_slot(path):
                pytest.fail("Second process admitted")
    finally:
        worker.kill()
        worker.communicate(timeout=5)
    with extraction_slot(path):
        pass
