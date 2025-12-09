-- Initialize pgvector extension for PKM database
-- This script runs automatically on first database initialization

CREATE EXTENSION IF NOT EXISTS vector;

-- Verify installation
DO $$
BEGIN
    RAISE NOTICE 'pgvector extension installed successfully';
END $$;
