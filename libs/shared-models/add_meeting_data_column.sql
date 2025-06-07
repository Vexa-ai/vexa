-- Migration: Add JSONB data column to meetings table
-- This migration adds a searchable JSONB column for storing meeting metadata

-- Add the data column with default empty JSON object
ALTER TABLE meetings ADD COLUMN IF NOT EXISTS data JSONB NOT NULL DEFAULT '{}'::jsonb;

-- Create GIN index for efficient searching of the JSONB data
CREATE INDEX IF NOT EXISTS ix_meeting_data_gin ON meetings USING gin (data);

-- Optional: Update existing meetings with empty data if NULL
UPDATE meetings SET data = '{}'::jsonb WHERE data IS NULL;

-- Verify the changes
SELECT column_name, data_type, is_nullable, column_default 
FROM information_schema.columns 
WHERE table_name = 'meetings' AND column_name = 'data';

-- Check the index was created
SELECT indexname, indexdef 
FROM pg_indexes 
WHERE tablename = 'meetings' AND indexname = 'ix_meeting_data_gin'; 