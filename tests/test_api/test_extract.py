from unittest.mock import MagicMock

from app.dependencies import get_pipeline, get_s3_service
from app.schemas.resume import PersonalInfo, ResumeData
from app.services.pipeline import PipelineResult


def test_extract_missing_api_key(client):
    """Requests without X-API-Key header return 401."""
    response = client.post("/api/v1/extract", files={"file": ("test.pdf", b"content")})
    assert response.status_code == 401


def test_extract_invalid_api_key(client):
    """Requests with invalid X-API-Key return 401."""
    response = client.post(
        "/api/v1/extract",
        files={"file": ("test.pdf", b"content")},
        headers={"X-API-Key": "invalid-key"},
    )
    assert response.status_code == 401


def test_extract_success(client, test_api_key):
    """Successful extraction returns 200 with resume data and URLs."""
    mock_pipeline = MagicMock()
    mock_pipeline.process_resume.return_value = PipelineResult(
        resume_data=ResumeData(
            personal_info=PersonalInfo(
                full_name="Jane Smith",
                email="jane@test.com",
                city="New York",
                country="USA",
            ),
            skills=["Python"],
        ),
        raw_text="Jane Smith\nSoftware Engineer\nPython",
        original_s3_key="originals/abc/test.pdf",
        generated_s3_key="generated/abc/test_normalized.pdf",
        processing_time_ms=1234.5,
    )

    mock_s3 = MagicMock()
    mock_s3.generate_presigned_url.return_value = "https://s3.example.com/file"

    client.app.dependency_overrides[get_pipeline] = lambda: mock_pipeline
    client.app.dependency_overrides[get_s3_service] = lambda: mock_s3

    response = client.post(
        "/api/v1/extract",
        files={"file": ("test.pdf", b"fake pdf content", "application/pdf")},
        headers={"X-API-Key": test_api_key},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["resume_data"]["personal_info"]["full_name"] == "Jane Smith"
    assert data["original_file_url"] == "https://s3.example.com/file"
    assert data["generated_pdf_url"] == "https://s3.example.com/file"
    assert data["raw_text"] == "Jane Smith\nSoftware Engineer\nPython"

    client.app.dependency_overrides.pop(get_pipeline, None)
    client.app.dependency_overrides.pop(get_s3_service, None)


def test_extract_forwards_hide_contact_info_flag(client, test_api_key):
    """The hide_contact_info query param is forwarded to the pipeline."""
    mock_pipeline = MagicMock()
    mock_pipeline.process_resume.return_value = PipelineResult(
        resume_data=ResumeData(personal_info=PersonalInfo(full_name="Jane Smith")),
        raw_text="Jane Smith",
        original_s3_key="originals/abc/test.pdf",
        generated_s3_key="generated/abc/test_normalized.pdf",
        processing_time_ms=10.0,
    )

    mock_s3 = MagicMock()
    mock_s3.generate_presigned_url.return_value = "https://s3.example.com/file"

    client.app.dependency_overrides[get_pipeline] = lambda: mock_pipeline
    client.app.dependency_overrides[get_s3_service] = lambda: mock_s3

    response = client.post(
        "/api/v1/extract?hide_contact_info=true",
        files={"file": ("test.pdf", b"fake pdf content", "application/pdf")},
        headers={"X-API-Key": test_api_key},
    )

    assert response.status_code == 200
    _, kwargs = mock_pipeline.process_resume.call_args
    assert kwargs["hide_contact_info"] is True

    client.app.dependency_overrides.pop(get_pipeline, None)
    client.app.dependency_overrides.pop(get_s3_service, None)


def test_extract_hide_contact_info_defaults_false(client, test_api_key):
    """Without the query param, hide_contact_info defaults to False."""
    mock_pipeline = MagicMock()
    mock_pipeline.process_resume.return_value = PipelineResult(
        resume_data=ResumeData(personal_info=PersonalInfo(full_name="Jane Smith")),
        raw_text="Jane Smith",
        original_s3_key="originals/abc/test.pdf",
        generated_s3_key="generated/abc/test_normalized.pdf",
        processing_time_ms=10.0,
    )

    mock_s3 = MagicMock()
    mock_s3.generate_presigned_url.return_value = "https://s3.example.com/file"

    client.app.dependency_overrides[get_pipeline] = lambda: mock_pipeline
    client.app.dependency_overrides[get_s3_service] = lambda: mock_s3

    response = client.post(
        "/api/v1/extract",
        files={"file": ("test.pdf", b"fake pdf content", "application/pdf")},
        headers={"X-API-Key": test_api_key},
    )

    assert response.status_code == 200
    _, kwargs = mock_pipeline.process_resume.call_args
    assert kwargs["hide_contact_info"] is False

    client.app.dependency_overrides.pop(get_pipeline, None)
    client.app.dependency_overrides.pop(get_s3_service, None)


def test_extract_with_raw_text(client, test_api_key):
    """When include_raw_text=true, raw text is included in response."""
    mock_pipeline = MagicMock()
    mock_pipeline.process_resume.return_value = PipelineResult(
        resume_data=ResumeData(
            personal_info=PersonalInfo(full_name="Jane Smith"),
            skills=["Python"],
        ),
        raw_text="Jane Smith\nSoftware Engineer",
        original_s3_key="originals/abc/test.pdf",
        generated_s3_key="generated/abc/test_normalized.pdf",
        processing_time_ms=500.0,
    )

    mock_s3 = MagicMock()
    mock_s3.generate_presigned_url.return_value = "https://s3.example.com/file"

    client.app.dependency_overrides[get_pipeline] = lambda: mock_pipeline
    client.app.dependency_overrides[get_s3_service] = lambda: mock_s3

    response = client.post(
        "/api/v1/extract",
        files={"file": ("test.pdf", b"fake pdf content", "application/pdf")},
        headers={"X-API-Key": test_api_key},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["raw_text"] == "Jane Smith\nSoftware Engineer"

    client.app.dependency_overrides.pop(get_pipeline, None)
    client.app.dependency_overrides.pop(get_s3_service, None)


def test_overlapping_extraction_is_rejected_and_health_stays_available(client, test_settings, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from unittest.mock import patch

    from app.auth.middleware import require_api_key
    from app.utils.exceptions import FileValidationError

    test_settings.EXTRACTION_LOCK_PATH = str(tmp_path / "extraction.lock")
    started, finish = Event(), Event()
    pipeline = MagicMock()
    s3 = MagicMock()
    s3.check_connectivity.return_value = True

    def process(*args, **kwargs):
        started.set()
        assert finish.wait(10), "Test did not release extraction"
        raise FileValidationError("Invalid test PDF")

    pipeline.process_resume.side_effect = process
    client.app.dependency_overrides[require_api_key] = lambda: None
    client.app.dependency_overrides[get_pipeline] = lambda: pipeline
    client.app.dependency_overrides[get_s3_service] = lambda: s3

    def request():
        return client.post("/api/v1/extract", files={"file": ("test.pdf", b"pdf")})

    with patch("app.dependencies.get_settings", return_value=test_settings):
        with ThreadPoolExecutor(max_workers=1) as pool:
            active = pool.submit(request)
            try:
                assert started.wait(5)
                busy = request()
                assert busy.status_code == 503
                assert busy.headers["Retry-After"] == "30"
                assert pipeline.process_resume.call_count == 1
                assert client.get("/api/v1/health").status_code == 200
            finally:
                finish.set()
            assert active.result(timeout=5).status_code == 415
        assert request().status_code == 415
        assert pipeline.process_resume.call_count == 2
