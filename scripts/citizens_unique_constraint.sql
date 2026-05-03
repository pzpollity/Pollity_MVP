-- Add unique constraint so upsert on (office_id, phone) works
-- Only adds constraint when phone is non-null
CREATE UNIQUE INDEX IF NOT EXISTS idx_citizens_office_phone
    ON citizens (office_id, phone)
    WHERE phone IS NOT NULL;
