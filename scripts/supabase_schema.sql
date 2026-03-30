-- =============================================================================
-- Jan-Sunwai · Supabase Schema
-- Run once against your Supabase project via the SQL Editor
-- =============================================================================

-- ── Extensions ────────────────────────────────────────────────────────────────
create extension if not exists "uuid-ossp";

-- ── offices ──────────────────────────────────────────────────────────────────
-- One row per elected representative / constituency office.
create table if not exists offices (
    id                  uuid primary key default uuid_generate_v4(),
    name                text not null,                  -- e.g. "Office of Shri Ramesh Sharma MLA"
    short_code          text not null unique,            -- e.g. "RSH" used in GR-RSH-2026-0001
    wa_phone_number_id  text unique,                     -- Meta Business phone_number_id
    created_at          timestamptz not null default now()
);

-- Sequence counter: one row per office, increments per new grievance
alter table offices add column if not exists sequence_counter integer not null default 0;

-- Critical alert contacts: notified immediately when a CRITICAL grievance is filed
alter table offices add column if not exists alert_whatsapp text;
alter table offices add column if not exists alert_emails   text[] default '{}';

-- ── staff ─────────────────────────────────────────────────────────────────────
-- Office staff who log in to the dashboard.
create table if not exists staff (
    id          uuid primary key default uuid_generate_v4(),
    office_id   uuid not null references offices(id) on delete cascade,
    name        text not null,
    role        text not null default 'staff',           -- 'admin' | 'staff'
    auth_user_id uuid unique,                            -- links to Supabase Auth
    created_at  timestamptz not null default now()
);

-- ── grievances ────────────────────────────────────────────────────────────────
create table if not exists grievances (
    id                  uuid primary key default uuid_generate_v4(),
    grievance_id        text not null unique,             -- GR-RSH-2026-0001
    office_id           uuid not null references offices(id) on delete cascade,

    -- Citizen info
    citizen_name        text,
    citizen_contact     text not null,                    -- WhatsApp E.164 or "WALK-IN"

    -- Intake
    channel             text not null,                    -- whatsapp | walk_in | phone | email | social_media | cpgrams
    raw_text            text not null,
    language_detected   text not null default 'en',

    -- Classification (from Claude)
    category            text not null,
    urgency             text not null,
    summary             text not null,
    is_duplicate        boolean not null default false,
    duplicate_of_id     uuid references grievances(id),

    -- Lifecycle
    status              text not null default 'registered',
    assigned_to         text,
    next_action         text,

    -- Timestamps
    filed_at            timestamptz not null default now(),
    updated_at          timestamptz not null default now(),
    closed_at           timestamptz
);

create index if not exists idx_grievances_office_status  on grievances(office_id, status);
create index if not exists idx_grievances_office_filed   on grievances(office_id, filed_at desc);
create index if not exists idx_grievances_urgency        on grievances(urgency);

-- ── Migration: OCR letter support (run once on existing deployments) ──────────
-- alter table grievances add column if not exists image_url text;

-- ── Migration: Location support (run once on existing deployments) ────────────
-- alter table grievances add column if not exists location_text text;
-- alter table grievances add column if not exists latitude     numeric(9,6);
-- alter table grievances add column if not exists longitude    numeric(9,6);
-- create index if not exists idx_grievances_location on grievances(office_id, location_text);

-- ── Row Level Security ────────────────────────────────────────────────────────
alter table offices    enable row level security;
alter table staff      enable row level security;
alter table grievances enable row level security;

-- Staff can only see their own office's data
-- The backend uses the service_role key (bypasses RLS) for webhook writes.
-- The dashboard uses the anon key with these policies for reads.

drop policy if exists "staff see own office grievances"  on grievances;
drop policy if exists "staff update own office grievances" on grievances;
drop policy if exists "staff see own office"               on offices;
drop policy if exists "staff see own profile"              on staff;

create policy "staff see own office grievances"
    on grievances for select
    using (
        office_id in (
            select office_id from staff
            where auth_user_id = auth.uid()
        )
    );

create policy "staff update own office grievances"
    on grievances for update
    using (
        office_id in (
            select office_id from staff
            where auth_user_id = auth.uid()
        )
    );

create policy "staff see own office"
    on offices for select
    using (
        id in (
            select office_id from staff
            where auth_user_id = auth.uid()
        )
    );

create policy "staff see own profile"
    on staff for select
    using (auth_user_id = auth.uid());

-- ── office_monthly_sequences ─────────────────────────────────────────────────
-- One row per (office, year_month). Atomically incremented; auto-resets each
-- month because each new year_month gets its own row starting at 1.
-- Replaces the old sequence_counter column on offices.
create table if not exists office_monthly_sequences (
    office_id   uuid    not null references offices(id) on delete cascade,
    year_month  text    not null,   -- e.g. '202603'
    counter     integer not null default 0,
    primary key (office_id, year_month)
);

-- ── increment_monthly_counter (RPC) ──────────────────────────────────────────
-- Atomically inserts or increments the counter for (office, year_month).
-- Returns the new sequence value. No race condition — uses ON CONFLICT DO UPDATE.
create or replace function increment_monthly_counter(
    office_id_param  uuid,
    year_month_param text
)
returns integer
language plpgsql
security definer
as $$
declare
    new_val integer;
begin
    insert into office_monthly_sequences (office_id, year_month, counter)
    values (office_id_param, year_month_param, 1)
    on conflict (office_id, year_month)
    do update set counter = office_monthly_sequences.counter + 1
    returning counter into new_val;
    return new_val;
end;
$$;

-- ── increment_grievance_counter (RPC) ────────────────────────────────────────
-- Legacy RPC kept for backwards compatibility during migration.
create or replace function increment_grievance_counter(office_id_param uuid)
returns integer
language plpgsql
security definer
as $$
declare
    new_val integer;
begin
    update offices
    set sequence_counter = sequence_counter + 1
    where id = office_id_param
    returning sequence_counter into new_val;
    return new_val;
end;
$$;

-- ── Seed: demo office (Phase 1 only — remove before production) ──────────────
-- Replace wa_phone_number_id with your actual Meta phone_number_id after setup.
insert into offices (id, name, short_code, wa_phone_number_id)
values (
    '00000000-0000-0000-0000-000000000001',
    'Demo Constituency Office',
    'DMO',
    '1019410701261103'
) on conflict (short_code) do update set wa_phone_number_id = EXCLUDED.wa_phone_number_id;

-- Set critical alert contacts for demo office
update offices
set alert_whatsapp = '+17739367759',
    alert_emails   = ARRAY['joe.ditommaso@pollity.in', 'piyush.zaware@pollity.in']
where short_code = 'DMO';
