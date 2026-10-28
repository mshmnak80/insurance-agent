import hashlib
from datetime import date
from decimal import Decimal

import pymupdf

from insurance_agent.ingest.adjuster_report import (
    extract_deductible,
    extract_key_values,
    extract_sections,
    get_document_info,
    parse_date,
    parse_money,
    parse_adjuster_report,
    split_adjuster,
)


REPORT_MARKDOWN = """
## Claim & Assignment
Claim number **C-1006** Policy number **MOT-0000045** Line of business **Motor**
Insured **Gianfranco Cilea** Adjuster **Rosalinde Albers (commercial)**
Date of loss **03/06/2025** Cause of loss **hail damage**
Disposition **OPEN — UNDER REVIEW** Reserve **€17,250.00**
Paid to date **€2,069.23**

## Findings
Inspection confirms hail damage.

## Recommendation
Coverage appears to apply, net of the €5,000.00 deductible.

This document is synthetic and generated for testing.
"""


def test_extract_key_values_reads_multiple_labels_on_same_line():
    assert extract_key_values(REPORT_MARKDOWN) == {
        "claim_id": "C-1006",
        "policy_number": "MOT-0000045",
        "line_of_business": "Motor",
        "insured_name": "Gianfranco Cilea",
        "adjuster_raw": "Rosalinde Albers (commercial)",
        "loss_date": "03/06/2025",
        "cause_of_loss": "hail damage",
        "disposition": "OPEN — UNDER REVIEW",
        "reserve_amount": "€17,250.00",
        "paid_to_date": "€2,069.23",
    }


def test_extract_sections_returns_requested_prose_sections_and_removes_footer():
    assert extract_sections(REPORT_MARKDOWN) == {
        "Findings": "Inspection confirms hail damage.",
        "Recommendation": "Coverage appears to apply, net of the €5,000.00 deductible.",
    }


def test_split_adjuster_extracts_type_when_present():
    assert split_adjuster("Rosalinde Albers (commercial)") == (
        "Rosalinde Albers",
        "commercial",
    )
    assert split_adjuster("Rosalinde Albers") == ("Rosalinde Albers", None)


def test_parse_date_and_money():
    assert parse_date("03/06/2025") == date(2025, 6, 3)
    assert parse_money("€17,250.00") == Decimal("17250.00")


def test_extract_deductible_from_recommendation():
    assert extract_deductible(
        {"Recommendation": "Payment is net of the €5,000.00 deductible."}
    ) == Decimal("5000.00")
    assert extract_deductible({"Recommendation": "No deductible applies."}) is None


def test_get_document_info_returns_database_document_fields(tmp_path):
    pdf_path = tmp_path / "C-1006-adjuster-report.pdf"
    with pymupdf.open() as pdf:
        pdf.new_page()
        pdf.new_page()
        pdf.save(pdf_path)

    document_info = get_document_info(
        REPORT_MARKDOWN,
        pdf_path,
        {"claim_id": "C-1006"},
        {},
    )

    assert document_info == {
        "claim_id": "C-1006",
        "doc_type": "adjuster_report",
        "file_name": pdf_path.name,
        "file_hash": hashlib.sha256(pdf_path.read_bytes()).hexdigest(),
        "page_count": 2,
        "doc_date": None,
    }


def test_parse_adjuster_report_returns_normalized_report_fields(tmp_path, monkeypatch):
    pdf_path = tmp_path / "C-1006-adjuster-report.pdf"
    with pymupdf.open() as pdf:
        pdf.new_page()
        pdf.save(pdf_path)
    monkeypatch.setattr(
        "insurance_agent.ingest.adjuster_report.pymupdf4llm.to_markdown",
        lambda _: REPORT_MARKDOWN,
    )

    reports = parse_adjuster_report(str(tmp_path))

    assert len(reports) == 1
    report = reports[0]
    assert report.fields["claim_id"] == "C-1006"
    assert report.fields["adjuster_name"] == "Rosalinde Albers"
    assert report.fields["adjuster_type"] == "commercial"
    assert report.fields["loss_date"] == date(2025, 6, 3)
    assert report.fields["reserve_amount"] == Decimal("17250.00")
    assert report.fields["paid_to_date"] == Decimal("2069.23")
    assert report.fields["deductible"] == Decimal("5000.00")
    assert report.document_info["page_count"] == 1
