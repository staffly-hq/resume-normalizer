from io import BytesIO

from PIL import Image
from reportlab.pdfgen import canvas

from app.services.pipeline import Pipeline
from app.services.text_extractor import extract_text_from_pdf


def test_pdf_rendering_preserves_pages_and_bounds_dimensions():
    source = BytesIO()
    pdf = canvas.Canvas(source, pagesize=(3000, 1500))
    for color in ((1, 0, 0), (0, 0, 1)):
        pdf.setFillColorRGB(*color)
        pdf.rect(0, 0, 3000, 1500, fill=1, stroke=0)
        pdf.showPage()
    pdf.save()

    pages = Pipeline._pdf_to_images(source.getvalue())
    assert len(pages) == 2
    for page, expected in zip(pages, [(255, 0, 0), (0, 0, 255)]):
        with Image.open(BytesIO(page)) as image:
            assert image.format == "PNG"
            assert image.size == (2000, 1000)
            assert image.convert("RGB").getpixel((100, 100)) == expected


def test_pdf_text_survives_page_cache_cleanup():
    source = BytesIO()
    pdf = canvas.Canvas(source)
    for text in ("First page", "Second page"):
        pdf.drawString(50, 700, text)
        pdf.showPage()
    pdf.save()
    assert extract_text_from_pdf(source.getvalue()) == "First page\nSecond page"
