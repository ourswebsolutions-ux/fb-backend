/*
# Fix RLS policies to allow service role bypass

## Problem
Background automation tasks use the service_role key (via get_supabase()).
The new user-scoped INSERT/UPDATE policies use WITH CHECK (user_id = auth.uid()).
For service_role, auth.uid() returns NULL, causing policy violations on INSERT.

## Fix
Add separate service_role bypass policies on all tables so background tasks
can write freely, while authenticated users are still scoped to their own data.
*/

-- ============================================================
-- fb_accounts — service role bypass
-- ============================================================
DROP POLICY IF EXISTS "service_role_all_fb_accounts" ON fb_accounts;
CREATE POLICY "service_role_all_fb_accounts" ON fb_accounts
  FOR ALL TO service_role USING (true) WITH CHECK (true);

-- ============================================================
-- listings — service role bypass
-- ============================================================
DROP POLICY IF EXISTS "service_role_all_listings" ON listings;
CREATE POLICY "service_role_all_listings" ON listings
  FOR ALL TO service_role USING (true) WITH CHECK (true);

-- ============================================================
-- tasks — service role bypass
-- ============================================================
DROP POLICY IF EXISTS "service_role_all_tasks" ON tasks;
CREATE POLICY "service_role_all_tasks" ON tasks
  FOR ALL TO service_role USING (true) WITH CHECK (true);

-- ============================================================
-- automation_logs — service role bypass
-- ============================================================
DROP POLICY IF EXISTS "service_role_all_automation_logs" ON automation_logs;
CREATE POLICY "service_role_all_automation_logs" ON automation_logs
  FOR ALL TO service_role USING (true) WITH CHECK (true);

-- ============================================================
-- inbox_messages — service role bypass
-- ============================================================
DROP POLICY IF EXISTS "service_role_all_inbox_messages" ON inbox_messages;
CREATE POLICY "service_role_all_inbox_messages" ON inbox_messages
  FOR ALL TO service_role USING (true) WITH CHECK (true);
