"""Generates a tiny synthetic PDF/docx corpus for the vertical-slice smoke test."""
import docx
import pymupdf as fitz

from pathlib import Path

HERE = Path(__file__).parent


def make_pdf():
    doc = fitz.open()
    page1 = doc.new_page()
    page1.insert_text((72, 72), "The invoice total for Acme Corp is $4,250 due March 3rd.")
    page2 = doc.new_page()
    page2.insert_text((72, 72), "Payment terms: net 30 days from the invoice date.")
    doc.save(HERE / "invoice_notes.pdf")
    doc.close()


def make_docx():
    doc = docx.Document()
    doc.add_paragraph(
        "Project kickoff meeting notes. The Everest project budget was approved "
        "at $120,000 for phase one. " * 30
    )
    doc.save(HERE / "meeting_notes.docx")


if __name__ == "__main__":
    make_pdf()
    make_docx()
    print("wrote", HERE / "invoice_notes.pdf", "and", HERE / "meeting_notes.docx")
