import pymupdf4llm

from pathlib import Path
from dataclasses import dataclass,field
from datetime import date, datetime
from decimal import Decimal
import re
import hashlib
import pymupdf
# Label in the PDF -> field name in our code
FIELD_LABELS = {
   "claim_id": "Claim number",
    "policy_number": "Policy number",
    "line_of_business": "Line of business",
    "insured_name": "Insured",
    "adjuster_raw": "Adjuster",
    "loss_date": "Date of loss",
    "cause_of_loss": "Cause of loss",
    "disposition": "Disposition",
    "reserve_amount": "Reserve",
    "paid_to_date": "Paid to date",
}

@dataclass
class AdjusterReport:
    fields: dict = field(default_factory=dict)
    sections: dict = field(default_factory=dict)
    document_info: dict = field(default_factory=dict)
    raw_markdown: str = ""



def clean(line:str) -> str:
    text = re.sub(r"[*_']","",line)
    text = text.strip("\t")
    return text


def extract_key_values(md:str)->dict:
    found = {}
    label_to_key = {label: key for key, label in FIELD_LABELS.items()}
    labels = "|".join(re.escape(label) for label in sorted(label_to_key, key=len, reverse=True))
    label_pattern = re.compile(rf"(?<!\w)(?P<label>{labels})(?!\w)")
    for line in md.splitlines():
        line = clean(line)
        matches = list(label_pattern.finditer(line))
        for index, match in enumerate(matches):
            key = label_to_key[match.group("label")]
            if key in found:
                continue
            value_end = matches[index + 1].start() if index + 1 < len(matches) else len(line)
            value = line[match.end():value_end].strip(" \t|:-")
            if value:
                found[key] = clean(value)

    return found

SECTION_NAMES = ["Findings", "Recommendation"]

def extract_sections(md:str)->dict:
    """Split on headings; keep only the prose sections we care about."""
    sections = {}
    # parts = [preamble, heading1, body1, heading2, body2, ...]
    parts = re.split(r"^#{1,6}\s*(.+?)\s*$", md, flags=re.MULTILINE)
    for heading, body in zip(parts[1::2], parts[2::2]):
        name = clean(heading)
        if name in SECTION_NAMES:
            body = re.sub(r"This document is synthetic.*$", "", body,
                          flags=re.DOTALL | re.IGNORECASE)  # drop footer
            sections[name] = " ".join(clean(body).split())
    return sections

def split_adjuster(raw: str) -> tuple[str, str | None]:
    """'Albano Llopis Hierro (liability)' -> ('Albano Llopis Hierro', 'liability')"""
    m = re.match(r"(.+?)\s*\((.+?)\)\s*$", raw)
    return (m.group(1).strip(), m.group(2).strip()) if m else (raw.strip(), None)

def parse_date(s: str) -> date:
    """DD/MM/YYYY (European). Change the format here if your data is US style."""
    return datetime.strptime(s.strip(), "%d/%m/%Y").date()

def parse_money(s: str) -> Decimal:
    """'€22,950.00' -> Decimal('22950.00')"""
    digits = re.sub(r"[^\d.,-]", "", s)
    return Decimal(digits.replace(",", ""))

def extract_deductible(sections: dict) -> Decimal | None:
    """Deductible only appears in prose: '...net of the €5,000.00 deductible.'"""
    text = sections.get("Recommendation", "")
    m = re.search(r"([€$£]\s?[\d,]+(?:\.\d{2})?)\s+deductible", text, re.IGNORECASE)
    return parse_money(m.group(1)) if m else None

def get_document_info(md: str, pdf_file: Path, fields: dict, sections: dict) -> dict:
    with pymupdf.open(pdf_file) as pdf:
        page_count = pdf.page_count

    document = {
        "claim_id":fields["claim_id"],
        "doc_type": "adjuster_report",
        "file_name": pdf_file.name,
        "file_hash": hashlib.sha256(pdf_file.read_bytes()).hexdigest(),
        "page_count": page_count,
        "doc_date": None,
    }
    return document
def parse_adjuster_report(loc:str):
    
    data_dir = Path(loc)
    adjuster_reports = []
    for pdf_file in data_dir.glob("*djuster-report*.pdf"):
        # create md file from pdf file
        md = pymupdf4llm.to_markdown(pdf_file)

        #get fields values
        adjuster_report_fields_values = extract_key_values(md)
        #print(adjuster_report_fields_values)
        
        #get sections
        sections = extract_sections(md)
        print(sections)

        #check for missing values 
        missing = [key for key in FIELD_LABELS if key not in adjuster_report_fields_values]
        if missing:
            raise ValueError(f"{pdf_file}: missing fields {missing}\n--- markdown ---\n{md}")
        
        name, adj_type = split_adjuster(adjuster_report_fields_values.pop("adjuster_raw"))
        fields = {
            **adjuster_report_fields_values,
            "adjuster_name": name,
            "adjuster_type": adj_type,
            "loss_date": parse_date(adjuster_report_fields_values["loss_date"]),
            "reserve_amount": parse_money(adjuster_report_fields_values["reserve_amount"]),
            "paid_to_date": parse_money(adjuster_report_fields_values["paid_to_date"]),
            "deductible": extract_deductible(sections),
            "currency": "EUR",
        }
        #get document info
        document_info = get_document_info(md,
                                          pdf_file,
                                          adjuster_report_fields_values,
                                          sections)
        adjuster_report = AdjusterReport(fields=fields, sections=sections, raw_markdown=md, document_info=document_info)

        adjuster_reports.append(adjuster_report)
        
    return adjuster_reports
