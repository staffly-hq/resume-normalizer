import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile

from app.auth.middleware import require_api_key
from app.auth.models import APIKey
from app.dependencies import get_pipeline, get_s3_service, get_settings
from app.config import Settings
from app.extraction_capacity import extraction_slot
from app.rate_limit import limiter
from app.schemas.response import ExtractResponse
from app.services.pipeline import Pipeline
from app.services.s3_service import S3Service
from app.utils.exceptions import (
    FileValidationError,
    LLMExtractionError,
    OCRError,
    PDFRenderError,
    S3Error,
    TextExtractionError,
)
from app.utils.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["extract"])


def _get_rate_limit_string() -> str:
    """Return the default rate limit. Per-key overrides stored in DB for future use."""
    from app.dependencies import get_settings
    settings = get_settings()
    return f"{settings.RATE_LIMIT_PER_MINUTE}/minute"


@router.post(
    "/extract",
    response_model=ExtractResponse,
    responses={503: {"description": "Extraction busy; retry after Retry-After seconds."}},
)
@limiter.limit(_get_rate_limit_string)
def extract_resume(
    request: Request,
    file: UploadFile,
    hide_contact_info: bool = Query(
        False,
        description=(
            "Omit the contact info line (email, location, phone, LinkedIn) "
            "from the generated PDF."
        ),
    ),
    api_key: APIKey = Depends(require_api_key),
    pipeline: Pipeline = Depends(get_pipeline),
    s3_service: S3Service = Depends(get_s3_service),
    settings: Settings = Depends(get_settings),
) -> ExtractResponse:
    """Upload a resume file and get structured data + standardized PDF URL."""
    with extraction_slot(settings.EXTRACTION_LOCK_PATH):
        request_id = str(uuid.uuid4())

        content = file.file.read(settings.max_upload_size_bytes + 1)
        if len(content) > settings.max_upload_size_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"File exceeds maximum size of {settings.MAX_UPLOAD_SIZE_MB}MB",
            )

        filename = file.filename or "unknown"

        try:
            result = pipeline.process_resume(content, filename, hide_contact_info=hide_contact_info)
        except FileValidationError as e:
            raise HTTPException(status_code=415, detail=str(e))
        except TextExtractionError as e:
            raise HTTPException(status_code=422, detail=str(e))
        except OCRError as e:
            logger.error("OCR failed", extra={"request_id": request_id, "error": str(e)})
            raise HTTPException(status_code=422, detail=f"OCR processing failed: {e}")
        except LLMExtractionError as e:
            logger.error("LLM extraction failed", extra={"request_id": request_id, "error": str(e)})
            raise HTTPException(status_code=502, detail=f"LLM processing failed: {e}")
        except PDFRenderError as e:
            logger.error("PDF render failed", extra={"request_id": request_id, "error": str(e)})
            raise HTTPException(status_code=500, detail=f"PDF generation failed: {e}")
        except S3Error as e:
            logger.error("S3 error", extra={"request_id": request_id, "error": str(e)})
            raise HTTPException(status_code=500, detail=f"Storage error: {e}")

        original_url = s3_service.generate_presigned_url(
            settings.S3_BUCKET_UPLOADS, result.original_s3_key
        )
        generated_url = s3_service.generate_presigned_url(
            settings.S3_BUCKET_GENERATED, result.generated_s3_key
        )

        return ExtractResponse(
            request_id=request_id,
            resume_data=result.resume_data,
            raw_text=result.raw_text,
            original_file_url=original_url,
            generated_pdf_url=generated_url,
            processing_time_ms=result.processing_time_ms,
        )
