"""
Ingest AdjusterReport objects into the claims / documents / chunks tables (Postgres + pgvector).

Setup:
    pip install psycopg2-binary pgvector openai numpy

Environment variables:
    OPENAI_API_KEY
    PGHOST (default localhost), PGPORT (default 5432),
    PGDATABASE, PGUSER, PGPASSWORD
"""
import hashlib
import os
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import psycopg2
from openai import OpenAI
from pgvector.psycopg2 import register_vector
from psycopg2.extras import Json, execute_values
from dotenv import load_dotenv
EMBED_MODEL = "text-embedding-3-small"  # 1536 dims, must match vector(1536)
EMBED_BATCH_SIZE = 100
CHUNK_SIZE = 1000
CHUNK_OVERLAP = 150
DOC_TYPE = "adjuster_report"

# strip stray whitespace/quotes from the key (a trailing newline causes
# "Illegal header value" / APIConnectionError)

load_dotenv("/root/.env")
_api_key = os.getenv("OPENAI_API_KEY", "").strip().strip('"').strip("'")
if not _api_key:
    raise ValueError("Please set your OPENAI_API_KEY environment variable.")
client = OpenAI(api_key=_api_key)


# --------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------
@dataclass
class AdjusterReport:
    fields: dict = field(default_factory=dict)         # claim-level data
    sections: dict = field(default_factory=dict)       # {"Scope of Loss": "text", ...}
    document_info: dict = field(default_factory=dict)  # file_name, page_count, doc_date
    raw_markdown: str = ""


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def embed(texts: List[str]) -> List[List[float]]:
    """Embed texts in batches."""
    vectors: List[List[float]] = []
    for i in range(0, len(texts), EMBED_BATCH_SIZE):
        batch = texts[i:i + EMBED_BATCH_SIZE]
        resp = client.embeddings.create(model=EMBED_MODEL, input=batch)
        vectors.extend(d.embedding for d in resp.data)
    return vectors


def split_long(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> List[str]:
    text = text.strip()
    if not text:
        return []
    out, i = [], 0
    while i < len(text):
        out.append(text[i:i + size])
        if i + size >= len(text):
            break
        i += size - overlap
    return out


def build_pieces(report: AdjusterReport) -> List[Tuple[Optional[str], str]]:
    """Return (section, text) pieces. Falls back to raw_markdown if no sections."""
    sections = report.sections or {"": report.raw_markdown}
    pieces: List[Tuple[Optional[str], str]] = []
    for section, text in sections.items():
        for part in split_long(text or ""):
            # prefix the section title so short chunks keep their context
            content = f"{section}\n{part}" if section else part
            pieces.append((section or None, content))
    return pieces


def get_connection():
    conn = psycopg2.connect(
        host=os.getenv("PGHOST", "localhost"),
        port=int(os.getenv("PGPORT", "5432")),
        dbname=os.getenv("PGDATABASE", "mydb"),
        user=os.getenv("PGUSER", "postgres"),
        password=os.getenv("PGPASSWORD", "mypassword"),
    )
    register_vector(conn)
    return conn


# --------------------------------------------------------------------------
# Ingestion
# --------------------------------------------------------------------------
def ingest_report(conn, report: AdjusterReport, doc_type: str = DOC_TYPE) -> bool:
    """
    Insert one report (claim -> document -> chunks) in a single transaction.
    Returns True if inserted, False if the document was already ingested.
    """
    info = report.document_info
    claim = {
        "claim_id": report.fields.get("claim_id"),
        "policy_number": report.fields.get("policy_number"),
        "claimant_name": report.fields.get("claimant_name"),
        "loss_date": report.fields.get("loss_date"),
        "loss_type": report.fields.get("loss_type"),
        "status": report.fields.get("status"),
    }
    if not claim["claim_id"]:
        raise ValueError("report.fields is missing 'claim_id'")

    file_hash = hashlib.sha256(report.raw_markdown.encode("utf-8")).hexdigest()

    pieces = build_pieces(report)
    if not pieces:
        raise ValueError(f"No text to ingest for claim {claim['claim_id']}")
    vectors = embed([text for _, text in pieces])  # embed before opening the transaction

    with conn:  # commits on success, rolls back on error
        with conn.cursor() as cur:
            # 1. claim (upsert)
            cur.execute(
                """
                INSERT INTO claims
                    (claim_id, policy_number, claimant_name, loss_date, loss_type, status, created_at)
                VALUES
                    (%(claim_id)s, %(policy_number)s, %(claimant_name)s, %(loss_date)s,
                     %(loss_type)s, %(status)s, now())
                ON CONFLICT (claim_id) DO UPDATE SET
                    policy_number = COALESCE(EXCLUDED.policy_number, claims.policy_number),
                    claimant_name = COALESCE(EXCLUDED.claimant_name, claims.claimant_name),
                    loss_date     = COALESCE(EXCLUDED.loss_date,     claims.loss_date),
                    loss_type     = COALESCE(EXCLUDED.loss_type,     claims.loss_type),
                    status        = COALESCE(EXCLUDED.status,        claims.status)
                """,
                claim,
            )

            # 2. document (skip if the same content was already ingested)
            cur.execute(
                """
                INSERT INTO documents
                    (claim_id, doc_type, file_name, file_hash, page_count, doc_date, metadata)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (file_hash) DO NOTHING
                RETURNING document_id
                """,
                (
                    claim["claim_id"],
                    doc_type,
                    info.get("file_name"),
                    file_hash,
                    info.get("page_count"),
                    info.get("doc_date"),
                    Json(info, dumps=lambda o: __import__("json").dumps(o, default=str)),
                ),
            )
            row = cur.fetchone()
            if row is None:
                return False
            document_id = row[0]

            # 3. chunks
            execute_values(
                cur,
                """
                INSERT INTO chunks
                    (document_id, claim_id, doc_type, chunk_index, section, content, embedding)
                VALUES %s
                """,
                [
                    (document_id, claim["claim_id"], doc_type, i, section, text, vec)
                    for i, ((section, text), vec) in enumerate(zip(pieces, vectors))
                ],
            )
    return True


def ingest_adjuster_reports(adjuster_reports: List[AdjusterReport]):
    """Ingest many reports. Each report has its own transaction, so one failure
    does not undo the others. Returns a list of (claim_id, error) for failures."""
    conn = get_connection()
    inserted, skipped, failed = 0, 0, []
    try:
        for report in adjuster_reports:
            claim_id = report.fields.get("claim_id", "<unknown>")
            try:
                if ingest_report(conn, report):
                    inserted += 1
                else:
                    skipped += 1
            except Exception as e:
                conn.rollback()
                failed.append((claim_id, str(e)))
                print(f"FAILED {claim_id}: {e}")
    finally:
        conn.close()

    print(f"Inserted: {inserted}, already present: {skipped}, failed: {len(failed)}")
    return failed


# --------------------------------------------------------------------------
# Retrieval example
# --------------------------------------------------------------------------
def search(question: str, claim_id: Optional[str] = None, k: int = 5):
    qvec = embed([question])[0]
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SET hnsw.ef_search = 100")  # helps when filtering by claim_id
            if claim_id:
                cur.execute(
                    """
                    SELECT claim_id, doc_type, section, content, embedding <=> %s AS distance
                    FROM chunks
                    WHERE claim_id = %s
                    ORDER BY embedding <=> %s
                    LIMIT %s
                    """,
                    (qvec, claim_id, qvec, k),
                )
            else:
                cur.execute(
                    """
                    SELECT claim_id, doc_type, section, content, embedding <=> %s AS distance
                    FROM chunks
                    ORDER BY embedding <=> %s
                    LIMIT %s
                    """,
                    (qvec, qvec, k),
                )
            return cur.fetchall()
    finally:
        conn.close()
