CREATE EXTENSION IF NOT EXISTS vector;--add pgvector extension

CREATE TABLE claims(
    claim_id text PRIMARY KEY,
    policy_number text,
    claimant_name text,
    loss_date date,
    loss_type text,
    status text,
    created_at TIMESTAMPTZ
);

CREATE TABLE documents(
    document_id serial PRIMARY KEY,
    claim_id    text NOT NULL REFERENCES claims(claim_id) ON DELETE CASCADE,
    doc_type     text NOT NULL CHECK (doc_type IN ('adjuster_report', 'estimate', 'first_notice_of_loss','settlement_letter')),
    file_name    text,
    file_hash    text,             -- sha256, so re-ingesting doesn't duplicate
    page_count   int,
    doc_date     date,
    metadata     jsonb DEFAULT '{}',
    ingested_at  timestamptz DEFAULT now(),
    UNIQUE (claim_id, doc_type),
    UNIQUE (file_hash)
);

CREATE TABLE chunks (
  chunk_id     serial PRIMARY KEY,
  document_id  int NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
  claim_id     text NOT NULL,    -- denormalized on purpose, for fast filtering
  doc_type     text NOT NULL,    -- denormalized on purpose
  chunk_index  int NOT NULL,
  page         int,
  section      text,
  content      text NOT NULL,
  embedding    vector(1536),     -- must match your embedding model
  UNIQUE (document_id, chunk_index)
);

CREATE INDEX ON chunks (claim_id);
CREATE INDEX ON chunks (doc_type);
CREATE INDEX ON chunks USING hnsw (embedding vector_cosine_ops);  --create hnsw index on embedding field in chunks table