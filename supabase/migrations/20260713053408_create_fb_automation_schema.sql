/*
# Facebook Automation Backend Schema

## Overview
Creates the core tables required for the Facebook Marketplace automation backend.
This is a single-tenant, no-auth app — all tables are accessible to anon + authenticated roles.

## New Tables

### 1. `fb_accounts`
Stores Facebook account credentials and status information.
- `id` — UUID primary key
- `email` — Facebook login email
- `password` — Encrypted account password
- `proxy` — Optional proxy string (ip:port:user:pass)
- `cookies` — Serialized browser cookies as JSON text
- `status` — Account status: 'active', 'banned', 'warming', 'idle'
- `warmup_level` — 0–100 warmup score
- `last_used_at` — Timestamp of last automation run
- `notes` — Free-text notes
- `created_at` — Row creation timestamp

### 2. `listings`
Stores marketplace listing data (drafts and published).
- `id` — UUID primary key
- `account_id` — FK to fb_accounts
- `title` — Listing title
- `description` — Listing description (AI-generated or manual)
- `price` — Price in USD cents (integer to avoid float issues)
- `category` — FB Marketplace category string
- `condition` — Item condition
- `images` — JSON array of image URLs
- `status` — 'draft', 'published', 'deleted', 'relisted'
- `fb_listing_id` — FB-side listing ID returned after publish
- `published_at` — Timestamp when listing went live
- `created_at` — Row creation timestamp

### 3. `automation_logs`
Audit log for every automation action taken.
- `id` — UUID primary key
- `task_id` — Optional FK to tasks table
- `account_id` — Optional FK to fb_accounts
- `action` — Short action name (e.g. 'publish_listing', 'warmup_scroll')
- `status` — 'success', 'failed', 'skipped'
- `details` — JSON object with action-specific metadata
- `error` — Error message if status is 'failed'
- `created_at` — Timestamp of log entry

### 4. `tasks`
Tracks background automation jobs and their progress.
- `id` — UUID primary key
- `type` — Task type matching feature name (e.g. 'new_account_slow', 'renew_listings')
- `status` — 'pending', 'running', 'completed', 'failed', 'cancelled'
- `input` — JSON object with task input parameters
- `result` — JSON object with task output/summary
- `progress` — 0–100 integer progress percentage
- `total_steps` — Total number of steps in the task
- `completed_steps` — Completed steps so far
- `error` — Top-level error message if task failed
- `started_at` — When the task started running
- `finished_at` — When the task completed or failed
- `created_at` — Row creation timestamp

## Security
- RLS enabled on all tables.
- All policies grant access to `anon` and `authenticated` roles (no sign-in required).
- `USING (true)` is intentional: single-tenant app with no user isolation.
*/

-- ============================================================
-- fb_accounts
-- ============================================================
CREATE TABLE IF NOT EXISTS fb_accounts (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  email         text NOT NULL,
  password      text NOT NULL,
  proxy         text,
  cookies       text,
  status        text NOT NULL DEFAULT 'idle'
                  CHECK (status IN ('active','banned','warming','idle')),
  warmup_level  integer NOT NULL DEFAULT 0 CHECK (warmup_level BETWEEN 0 AND 100),
  last_used_at  timestamptz,
  notes         text,
  created_at    timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE fb_accounts ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "anon_select_fb_accounts" ON fb_accounts;
CREATE POLICY "anon_select_fb_accounts" ON fb_accounts FOR SELECT
  TO anon, authenticated USING (true);

DROP POLICY IF EXISTS "anon_insert_fb_accounts" ON fb_accounts;
CREATE POLICY "anon_insert_fb_accounts" ON fb_accounts FOR INSERT
  TO anon, authenticated WITH CHECK (true);

DROP POLICY IF EXISTS "anon_update_fb_accounts" ON fb_accounts;
CREATE POLICY "anon_update_fb_accounts" ON fb_accounts FOR UPDATE
  TO anon, authenticated USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "anon_delete_fb_accounts" ON fb_accounts;
CREATE POLICY "anon_delete_fb_accounts" ON fb_accounts FOR DELETE
  TO anon, authenticated USING (true);

-- ============================================================
-- listings
-- ============================================================
CREATE TABLE IF NOT EXISTS listings (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  account_id    uuid REFERENCES fb_accounts(id) ON DELETE SET NULL,
  title         text NOT NULL,
  description   text,
  price         integer NOT NULL DEFAULT 0,
  category      text,
  condition     text DEFAULT 'used_good',
  images        jsonb NOT NULL DEFAULT '[]'::jsonb,
  status        text NOT NULL DEFAULT 'draft'
                  CHECK (status IN ('draft','published','deleted','relisted')),
  fb_listing_id text,
  published_at  timestamptz,
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_listings_account_id ON listings(account_id);
CREATE INDEX IF NOT EXISTS idx_listings_status     ON listings(status);

ALTER TABLE listings ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "anon_select_listings" ON listings;
CREATE POLICY "anon_select_listings" ON listings FOR SELECT
  TO anon, authenticated USING (true);

DROP POLICY IF EXISTS "anon_insert_listings" ON listings;
CREATE POLICY "anon_insert_listings" ON listings FOR INSERT
  TO anon, authenticated WITH CHECK (true);

DROP POLICY IF EXISTS "anon_update_listings" ON listings;
CREATE POLICY "anon_update_listings" ON listings FOR UPDATE
  TO anon, authenticated USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "anon_delete_listings" ON listings;
CREATE POLICY "anon_delete_listings" ON listings FOR DELETE
  TO anon, authenticated USING (true);

-- ============================================================
-- tasks
-- ============================================================
CREATE TABLE IF NOT EXISTS tasks (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  type             text NOT NULL,
  status           text NOT NULL DEFAULT 'pending'
                     CHECK (status IN ('pending','running','completed','failed','cancelled')),
  input            jsonb NOT NULL DEFAULT '{}'::jsonb,
  result           jsonb,
  progress         integer NOT NULL DEFAULT 0 CHECK (progress BETWEEN 0 AND 100),
  total_steps      integer NOT NULL DEFAULT 0,
  completed_steps  integer NOT NULL DEFAULT 0,
  error            text,
  started_at       timestamptz,
  finished_at      timestamptz,
  created_at       timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_type   ON tasks(type);

ALTER TABLE tasks ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "anon_select_tasks" ON tasks;
CREATE POLICY "anon_select_tasks" ON tasks FOR SELECT
  TO anon, authenticated USING (true);

DROP POLICY IF EXISTS "anon_insert_tasks" ON tasks;
CREATE POLICY "anon_insert_tasks" ON tasks FOR INSERT
  TO anon, authenticated WITH CHECK (true);

DROP POLICY IF EXISTS "anon_update_tasks" ON tasks;
CREATE POLICY "anon_update_tasks" ON tasks FOR UPDATE
  TO anon, authenticated USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "anon_delete_tasks" ON tasks;
CREATE POLICY "anon_delete_tasks" ON tasks FOR DELETE
  TO anon, authenticated USING (true);

-- ============================================================
-- automation_logs
-- ============================================================
CREATE TABLE IF NOT EXISTS automation_logs (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  task_id     uuid REFERENCES tasks(id) ON DELETE SET NULL,
  account_id  uuid REFERENCES fb_accounts(id) ON DELETE SET NULL,
  action      text NOT NULL,
  status      text NOT NULL DEFAULT 'success'
                CHECK (status IN ('success','failed','skipped')),
  details     jsonb NOT NULL DEFAULT '{}'::jsonb,
  error       text,
  created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_automation_logs_task_id    ON automation_logs(task_id);
CREATE INDEX IF NOT EXISTS idx_automation_logs_account_id ON automation_logs(account_id);
CREATE INDEX IF NOT EXISTS idx_automation_logs_action     ON automation_logs(action);

ALTER TABLE automation_logs ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "anon_select_automation_logs" ON automation_logs;
CREATE POLICY "anon_select_automation_logs" ON automation_logs FOR SELECT
  TO anon, authenticated USING (true);

DROP POLICY IF EXISTS "anon_insert_automation_logs" ON automation_logs;
CREATE POLICY "anon_insert_automation_logs" ON automation_logs FOR INSERT
  TO anon, authenticated WITH CHECK (true);

DROP POLICY IF EXISTS "anon_update_automation_logs" ON automation_logs;
CREATE POLICY "anon_update_automation_logs" ON automation_logs FOR UPDATE
  TO anon, authenticated USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "anon_delete_automation_logs" ON automation_logs;
CREATE POLICY "anon_delete_automation_logs" ON automation_logs FOR DELETE
  TO anon, authenticated USING (true);
