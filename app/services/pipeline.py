from __future__ import annotations

import time
from dataclasses import dataclass

from app.config import Settings
from app.schemas.resume import ResumeData
from app.services.file_detector import FileType, detect_file_type
from app.services.llm_service import LLMService
from app.services.pdf_renderer import render_resume_pdf
from app.services.s3_service import S3Service
from app.services.text_extractor import extract_text_from_docx, extract_text_from_pdf
from app.utils.exceptions import TextExtractionError
from app.utils.logging import get_logger

logger = get_logger(__name__)

MIME_FOR_FILE_TYPE = {
    FileType.PDF: "application/pdf",
    FileType.DOCX: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


@dataclass
class PipelineResult:
    resume_data: ResumeData
    raw_text: str
    original_s3_key: str
    generated_s3_key: str
    processing_time_ms: float


class Pipeline:
    def __init__(self, settings: Settings, s3_service: S3Service, llm_service: LLMService):
        self.settings = settings
        self.s3 = s3_service
        self.llm = llm_service

    def process_resume(
        self, file_content: bytes, filename: str, hide_contact_info: bool = False
    ) -> PipelineResult:
        """Run the full resume processing pipeline."""
        start = time.monotonic()

        # 1. Detect file type
        file_type = detect_file_type(
            file_content, filename, self.settings.ALLOWED_MIME_TYPES
        )
        logger.info("Pipeline started", extra={"file_name": filename, "file_type": file_type.value})

        # 2. Upload original to S3
        content_type = MIME_FOR_FILE_TYPE.get(file_type, self._detect_image_mime(filename))
        original_s3_key = self.s3.upload_original(file_content, filename, content_type)

        # 3. Extract text and structured data based on file type
        raw_text, resume_data = self._extract_and_parse(file_content, file_type, filename)

        # 6. Render standardized PDF
        pdf_bytes = render_resume_pdf(resume_data, hide_contact_info=hide_contact_info)

        # 7. Upload generated PDF to S3
        candidate_name = (
            resume_data.personal_info.full_name
            if resume_data.personal_info and resume_data.personal_info.full_name
            else "Unknown"
        )
        generated_s3_key = self.s3.upload_generated_pdf(pdf_bytes, candidate_name)

        elapsed_ms = (time.monotonic() - start) * 1000
        logger.info("Pipeline complete", extra={"elapsed_ms": round(elapsed_ms, 1)})

        return PipelineResult(
            resume_data=resume_data,
            raw_text=raw_text,
            original_s3_key=original_s3_key,
            generated_s3_key=generated_s3_key,
            processing_time_ms=round(elapsed_ms, 1),
        )

    @staticmethod
    def _detect_image_mime(filename: str) -> str:
        """Detect image MIME type from filename extension."""
        if filename.lower().endswith((".jpg", ".jpeg")):
            return "image/jpeg"
        return "image/png"

    def _extract_and_parse(
        self, file_content: bytes, file_type: FileType, filename: str
    ) -> tuple[str, ResumeData]:
        """Extract text and structured data. Uses LLM vision only for images."""
        if file_type == FileType.IMAGE:
            logger.info("Using LLM vision for image resume")
            mime = self._detect_image_mime(filename)
            resume_data = self.llm.extract_resume_data_from_images([file_content], mime_type=mime)
            return "[extracted via vision]", resume_data

        if file_type == FileType.DOCX:
            raw_text = extract_text_from_docx(file_content)
            self._validate_text(raw_text, filename)
            resume_data = self.llm.extract_resume_data(raw_text)
            return raw_text, resume_data

        # PDF: try text extraction first, fall back to vision for image-based PDFs
        raw_text = extract_text_from_pdf(file_content)
        if len(raw_text.strip()) < self.settings.OCR_MIN_TEXT_LENGTH:
            logger.info(
                "PDF text extraction insufficient, routing to vision",
                extra={"extracted_chars": len(raw_text.strip())},
            )
            page_images = self._pdf_to_images(file_content)
            resume_data = self.llm.extract_resume_data_from_images(page_images, mime_type="image/png")
            return "[extracted via vision from image-based PDF]", resume_data

        logger.info("Text extraction complete", extra={"chars": len(raw_text)})
        resume_data = self.llm.extract_resume_data(raw_text)
        return raw_text, resume_data

    @staticmethod
    def _pdf_to_images(file_content: bytes) -> list[bytes]:
        """Convert PDF pages to PNG byte arrays for vision processing."""
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from pdf2image import convert_from_bytes

        with TemporaryDirectory(prefix="resume-pages-") as output_dir:
            paths = convert_from_bytes(
                file_content,
                fmt="png",
                output_folder=output_dir,
                paths_only=True,
                size=2000,
                thread_count=1,
            )
            return [Path(path).read_bytes() for path in paths]

    def _validate_text(self, raw_text: str, filename: str) -> None:
        """Validate extracted text has enough content."""
        if len(raw_text.strip()) < self.settings.OCR_MIN_TEXT_LENGTH:
            raise TextExtractionError(
                f"Extracted text too short ({len(raw_text.strip())} chars) "
                f"after all extraction attempts for '{filename}'"
            )
