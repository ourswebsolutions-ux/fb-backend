/*
# Fix orphaned inbox_messages.account_id

## Problem
inbox_messages.account_id used ON DELETE SET NULL, so deleting/recreating fb_accounts
left messages with account_id = NULL. The admin panel filters by account_id and shows 0 rows.

## Changes
1. Backfill account_id from automation_logs + tasks where possible
2. Assign remaining orphans to the sole fb_account when only one exists
3. Change FK to ON DELETE CASCADE so messages stay tied to their account lifecycle
*/

-- 1) Backfill from automation_logs that still have account_id
UPDATE inbox_messages im
SET account_id = al.account_id
FROM automation_logs al
WHERE im.account_id IS NULL
  AND al.account_id IS NOT NULL
  AND al.action = 'inbox_read_message'
  AND al.details->>'sender' = im.sender_name
  AND im.created_at BETWEEN al.created_at - interval '3 seconds'
                      AND al.created_at + interval '3 seconds';

-- 2) Backfill from tasks.input when log.account_id was null (deleted account scenario)
UPDATE inbox_messages im
SET account_id = sub.account_id
FROM (
  SELECT DISTINCT ON (im2.id)
    im2.id AS message_id,
    (t.input->>'account_id')::uuid AS account_id
  FROM inbox_messages im2
  JOIN automation_logs al ON al.action = 'inbox_read_message'
    AND al.details->>'sender' = im2.sender_name
    AND im2.created_at BETWEEN al.created_at - interval '3 seconds'
                           AND al.created_at + interval '3 seconds'
  JOIN tasks t ON t.id = al.task_id
  WHERE im2.account_id IS NULL
    AND t.input->>'account_id' ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
    AND EXISTS (
      SELECT 1 FROM fb_accounts fa
      WHERE fa.id = (t.input->>'account_id')::uuid
    )
  ORDER BY im2.id, al.created_at DESC
) sub
WHERE im.id = sub.message_id
  AND im.account_id IS NULL;

-- 3) Single-tenant fallback: assign orphans to the only remaining account
UPDATE inbox_messages im
SET account_id = fa.id
FROM fb_accounts fa
WHERE im.account_id IS NULL
  AND (SELECT count(*) FROM fb_accounts) = 1;

-- 4) Tighten FK — cascade delete messages with their account instead of orphaning
ALTER TABLE inbox_messages
  DROP CONSTRAINT IF EXISTS inbox_messages_account_id_fkey;

ALTER TABLE inbox_messages
  ADD CONSTRAINT inbox_messages_account_id_fkey
  FOREIGN KEY (account_id) REFERENCES fb_accounts(id) ON DELETE CASCADE;
