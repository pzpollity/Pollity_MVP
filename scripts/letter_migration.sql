-- =============================================================================
-- Jan-Sunwai · D.O. Letter Feature Migration
-- Run once via Supabase SQL Editor (or psql)
-- =============================================================================

-- ── 1. Add letter_profile JSONB to offices ────────────────────────────────────
-- Stores all letterhead data: rep name, title, address, D.O. prefix, etc.
-- Field is nullable / defaults to empty JSONB so existing offices are unaffected.
ALTER TABLE offices ADD COLUMN IF NOT EXISTS letter_profile JSONB DEFAULT '{}'::jsonb;

-- ── 2. Seed the demo office with a placeholder letter profile ─────────────────
-- Replace every [PLACEHOLDER] value with real data before going to production.
UPDATE offices
SET letter_profile = jsonb_build_object(
  'rep_name',            '[REPRESENTATIVE NAME]',
  'rep_name_hindi',      '[REPRESENTATIVE NAME IN HINDI]',
  'rep_designation',     '[MLA / MP / Councillor]',
  'rep_full_title',      '[Full official title of the representative]',
  'sender_name',         '[Name of PS/PA signing the letter]',
  'sender_role_english', 'PRIVATE SECRETARY TO [REPRESENTATIVE TITLE]',
  'sender_role_hindi',   '[Role in Hindi]',
  'office_address',      '[Office Address, City - PIN Code]',
  'office_phone',        '[Phone Number]',
  'office_fax',          '[Fax Number]',
  'office_email',        '[official@gov.in]',
  'do_prefix',           'OFF/25'
)
WHERE short_code = 'DMO';

-- ── 3. Letters log table ──────────────────────────────────────────────────────
-- Tracks every generated D.O. letter:
--   - provides the counter for D.O. number sequencing (COUNT + 1)
--   - stores the full rendered HTML for re-download / audit
--   - links back to the grievance for traceability
CREATE TABLE IF NOT EXISTS letters_log (
    id              UUID         DEFAULT uuid_generate_v4() PRIMARY KEY,
    office_id       UUID         NOT NULL REFERENCES offices(id)    ON DELETE CASCADE,
    grievance_id    UUID                  REFERENCES grievances(id) ON DELETE SET NULL,
    do_number       TEXT         NOT NULL,
    letter_type     TEXT         NOT NULL DEFAULT 'do_standard',
    addressee_name  TEXT,
    html_content    TEXT,
    generated_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_letters_log_office     ON letters_log(office_id);
CREATE INDEX IF NOT EXISTS idx_letters_log_grievance  ON letters_log(grievance_id);
CREATE INDEX IF NOT EXISTS idx_letters_log_generated  ON letters_log(generated_at DESC);

-- ── 4. Row Level Security for letters_log ─────────────────────────────────────
ALTER TABLE letters_log ENABLE ROW LEVEL SECURITY;

-- Staff can read letters for their own office
DROP POLICY IF EXISTS "staff see own office letters" ON letters_log;
CREATE POLICY "staff see own office letters"
    ON letters_log FOR SELECT
    USING (
        office_id IN (
            SELECT office_id FROM staff
            WHERE auth_user_id = auth.uid()
        )
    );

-- Staff can insert letters for their own office
DROP POLICY IF EXISTS "staff insert own office letters" ON letters_log;
CREATE POLICY "staff insert own office letters"
    ON letters_log FOR INSERT
    WITH CHECK (
        office_id IN (
            SELECT office_id FROM staff
            WHERE auth_user_id = auth.uid()
        )
    );

-- =============================================================================
-- NOTES
-- =============================================================================
-- The backend uses the service_role key (bypasses RLS) for all INSERT operations.
-- The dashboard uses the anon key, which is governed by the policies above.
--
-- D.O. Number format:   {do_prefix}/{year}-{counter:04d}
--   e.g.  OFF/25/2026-0003
--   counter = COUNT(letters_log WHERE office_id = ?) + 1
--   This is computed server-side in letter_generator.py.
--
-- To add a real office profile later, run:
--   UPDATE offices SET letter_profile = '{ ... }'::jsonb WHERE short_code = 'YOUR_CODE';
-- =============================================================================
