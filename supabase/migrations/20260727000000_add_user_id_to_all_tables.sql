/*
# Add user_id to all tables for multi-user data isolation

## Changes
- Adds `user_id` (uuid, nullable initially) to: fb_accounts, listings, tasks, automation_logs, inbox_messages
- Creates indexes on user_id for query performance
- Updates RLS policies so each user can only access their own rows
- Existing rows (if any) will have user_id = NULL and will be invisible after policy change.
  Run the backfill note below if you need to assign existing rows to a user.

## Backfill (optional — only if you have existing data)
  UPDATE fb_accounts SET user_id = '<your-user-uuid>' WHERE user_id IS NULL;
  UPDATE listings      SET user_id = '<your-user-uuid>' WHERE user_id IS NULL;
  UPDATE tasks         SET user_id = '<your-user-uuid>' WHERE user_id IS NULL;
  UPDATE automation_logs SET user_id = '<your-user-uuid>' WHERE user_id IS NULL;
  UPDATE inbox_messages  SET user_id = '<your-user-uuid>' WHERE user_id IS NULL;
*/

-- ============================================================
-- fb_accounts — add user_id
-- ============================================================
ALTER TABLE fb_accounts
  ADD COLUMN IF NOT EXISTS user_id uuid REFERENCES auth.users(id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS idx_fb_accounts_user_id ON fb_accounts(user_id);

-- Drop old open-access policies
DROP POLICY IF EXISTS "anon_select_fb_accounts"  ON fb_accounts;
DROP POLICY IF EXISTS "anon_insert_fb_accounts"  ON fb_accounts;
DROP POLICY IF EXISTS "anon_update_fb_accounts"  ON fb_accounts;
DROP POLICY IF EXISTS "anon_delete_fb_accounts"  ON fb_accounts;

-- New user-scoped policies
CREATE POLICY "users_select_own_fb_accounts" ON fb_accounts
  FOR SELECT TO authenticated USING (user_id = auth.uid());

CREATE POLICY "users_insert_own_fb_accounts" ON fb_accounts
  FOR INSERT TO authenticated WITH CHECK (user_id = auth.uid());

CREATE POLICY "users_update_own_fb_accounts" ON fb_accounts
  FOR UPDATE TO authenticated USING (user_id = auth.uid()) WITH CHECK (user_id = auth.uid());

CREATE POLICY "users_delete_own_fb_accounts" ON fb_accounts
  FOR DELETE TO authenticated USING (user_id = auth.uid());


-- ============================================================
-- listings — add user_id
-- ============================================================
ALTER TABLE listings
  ADD COLUMN IF NOT EXISTS user_id uuid REFERENCES auth.users(id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS idx_listings_user_id ON listings(user_id);

DROP POLICY IF EXISTS "anon_select_listings"  ON listings;
DROP POLICY IF EXISTS "anon_insert_listings"  ON listings;
DROP POLICY IF EXISTS "anon_update_listings"  ON listings;
DROP POLICY IF EXISTS "anon_delete_listings"  ON listings;

CREATE POLICY "users_select_own_listings" ON listings
  FOR SELECT TO authenticated USING (user_id = auth.uid());

CREATE POLICY "users_insert_own_listings" ON listings
  FOR INSERT TO authenticated WITH CHECK (user_id = auth.uid());

CREATE POLICY "users_update_own_listings" ON listings
  FOR UPDATE TO authenticated USING (user_id = auth.uid()) WITH CHECK (user_id = auth.uid());

CREATE POLICY "users_delete_own_listings" ON listings
  FOR DELETE TO authenticated USING (user_id = auth.uid());


-- ============================================================
-- tasks — add user_id
-- ============================================================
ALTER TABLE tasks
  ADD COLUMN IF NOT EXISTS user_id uuid REFERENCES auth.users(id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS idx_tasks_user_id ON tasks(user_id);

DROP POLICY IF EXISTS "anon_select_tasks"  ON tasks;
DROP POLICY IF EXISTS "anon_insert_tasks"  ON tasks;
DROP POLICY IF EXISTS "anon_update_tasks"  ON tasks;
DROP POLICY IF EXISTS "anon_delete_tasks"  ON tasks;

CREATE POLICY "users_select_own_tasks" ON tasks
  FOR SELECT TO authenticated USING (user_id = auth.uid());

CREATE POLICY "users_insert_own_tasks" ON tasks
  FOR INSERT TO authenticated WITH CHECK (user_id = auth.uid());

CREATE POLICY "users_update_own_tasks" ON tasks
  FOR UPDATE TO authenticated USING (user_id = auth.uid()) WITH CHECK (user_id = auth.uid());

CREATE POLICY "users_delete_own_tasks" ON tasks
  FOR DELETE TO authenticated USING (user_id = auth.uid());


-- ============================================================
-- automation_logs — add user_id
-- ============================================================
ALTER TABLE automation_logs
  ADD COLUMN IF NOT EXISTS user_id uuid REFERENCES auth.users(id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS idx_automation_logs_user_id ON automation_logs(user_id);

DROP POLICY IF EXISTS "anon_select_automation_logs"  ON automation_logs;
DROP POLICY IF EXISTS "anon_insert_automation_logs"  ON automation_logs;
DROP POLICY IF EXISTS "anon_update_automation_logs"  ON automation_logs;
DROP POLICY IF EXISTS "anon_delete_automation_logs"  ON automation_logs;

CREATE POLICY "users_select_own_automation_logs" ON automation_logs
  FOR SELECT TO authenticated USING (user_id = auth.uid());

CREATE POLICY "users_insert_own_automation_logs" ON automation_logs
  FOR INSERT TO authenticated WITH CHECK (user_id = auth.uid());

CREATE POLICY "users_update_own_automation_logs" ON automation_logs
  FOR UPDATE TO authenticated USING (user_id = auth.uid()) WITH CHECK (user_id = auth.uid());

CREATE POLICY "users_delete_own_automation_logs" ON automation_logs
  FOR DELETE TO authenticated USING (user_id = auth.uid());


-- ============================================================
-- inbox_messages — add user_id
-- ============================================================
ALTER TABLE inbox_messages
  ADD COLUMN IF NOT EXISTS user_id uuid REFERENCES auth.users(id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS idx_inbox_messages_user_id ON inbox_messages(user_id);

DROP POLICY IF EXISTS "anon_select_inbox_messages"  ON inbox_messages;
DROP POLICY IF EXISTS "anon_insert_inbox_messages"  ON inbox_messages;
DROP POLICY IF EXISTS "anon_update_inbox_messages"  ON inbox_messages;
DROP POLICY IF EXISTS "anon_delete_inbox_messages"  ON inbox_messages;

CREATE POLICY "users_select_own_inbox_messages" ON inbox_messages
  FOR SELECT TO authenticated USING (user_id = auth.uid());

CREATE POLICY "users_insert_own_inbox_messages" ON inbox_messages
  FOR INSERT TO authenticated WITH CHECK (user_id = auth.uid());

CREATE POLICY "users_update_own_inbox_messages" ON inbox_messages
  FOR UPDATE TO authenticated USING (user_id = auth.uid()) WITH CHECK (user_id = auth.uid());

CREATE POLICY "users_delete_own_inbox_messages" ON inbox_messages
  FOR DELETE TO authenticated USING (user_id = auth.uid());
