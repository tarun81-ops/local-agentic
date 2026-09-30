from app.config import settings
from app.core.parsers import image_parser
from app.core.parsers.docx_parser import parse_docx
from app.core.parsers.pdf_parser import parse_pdf
from app.core.parsers.pptx_parser import parse_pptx
from app.core.parsers.text_parser import parse_email, parse_html, parse_text
from app.core.parsers.xlsx_parser import parse_xlsx

PARSERS = {
    ".pdf": parse_pdf, ".docx": parse_docx, ".pptx": parse_pptx, ".xlsx": parse_xlsx,
    ".txt": parse_text, ".md": parse_text, ".csv": parse_text,
    ".html": parse_html, ".htm": parse_html, ".eml": parse_email,
}

if image_parser.available() or settings.caption_images:
    PARSERS.update({ext: image_parser.parse_image for ext in (".png", ".jpg", ".jpeg", ".webp", ".bmp")})
