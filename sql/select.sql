SELECT chunk_id,
       document_id,
       claim_id,
       doc_type,
       chunk_index,
       page,
       section,
       content,
       embedding
FROM public.chunks
LIMIT 1000;


-- SELECT COUNT(*)
-- FROM public.chunks
-- WHERE section ='Findings';

-- Example: filter chunks for one claim and section.
-- SELECT chunk_id,
--        claim_id,
--        section,
--        content
-- FROM public.chunks
-- WHERE claim_id = 'C-1006'
--   AND section = 'Findings'
-- LIMIT 1000;

-- Example: find chunks with no section assigned.
-- SELECT chunk_id,
--        claim_id,
--        section,
--        content
-- FROM public.chunks
-- WHERE section IS NULL
-- LIMIT 1000;