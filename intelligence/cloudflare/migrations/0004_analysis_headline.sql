-- Editorial headline is distinct from the original source title and summary.
-- Existing analyses retain NULL and use the legacy renderer fallback.
ALTER TABLE analyses ADD COLUMN headline TEXT;
