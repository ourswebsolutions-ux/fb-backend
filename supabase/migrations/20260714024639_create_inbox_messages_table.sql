/*
# Inbox Messages Table

## Overview
Adds a table to store Facebook Marketplace inbox messages and AI-generated replies.
This supports the inbox auto-read and auto-reply feature.

## New Tables

### `inbox_messages`
Stores messages read from Facebook Marketplace inbox and replies sent.
- `id` — UUID primary key
- `account_id` — FK to fb_accounts (which FB account received the message)
- `thread_id` — Facebook conversation thread ID
- `sender_name` — Name of the person who sent the message
- `sender_id` — Facebook user ID of sender
- `message_text` — The incoming message body
- `reply_text` — The AI-generated reply (null if not yet replied)
- `reply_status` — 'pending', 'sent', 'failed', 'skipped'
- `listing_id` — Optional FK to listings if message is about a specific listing
- `read_at` — Timestamp when the message was read from inbox
- `replied_at` — Timestamp when the reply was sent
- `created_at` — Row creation timestamp

## Security
- RLS enabled.
- Anon + authenticated CRUD (single-tenant, no auth on this table itself).
*/

CREATE TABLE IF NOT EXISTS inbox_messages (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  account_id    uuid REFERENCES fb_accounts(id) ON DELETE SET NULL,
  thread_id     text,
  sender_name   text,
  sender_id     text,
  message_text  text NOT NULL,
  reply_text    text,
  reply_status  text NOT NULL DEFAULT 'pending'
                  CHECK (reply_status IN ('pending','sent','failed','skipped')),
  listing_id    uuid REFERENCES listings(id) ON DELETE SET NULL,
  read_at       timestamptz,
  replied_at    timestamptz,
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_inbox_messages_account_id ON inbox_messages(account_id);
CREATE INDEX IF NOT EXISTS idx_inbox_messages_reply_status ON inbox_messages(reply_status);
CREATE INDEX IF NOT EXISTS idx_inbox_messages_thread_id ON inbox_messages(thread_id);

ALTER TABLE inbox_messages ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "anon_select_inbox_messages" ON inbox_messages;
CREATE POLICY "anon_select_inbox_messages" ON inbox_messages FOR SELECT
  TO anon, authenticated USING (true);

DROP POLICY IF EXISTS "anon_insert_inbox_messages" ON inbox_messages;
CREATE POLICY "anon_insert_inbox_messages" ON inbox_messages FOR INSERT
  TO anon, authenticated WITH CHECK (true);

DROP POLICY IF EXISTS "anon_update_inbox_messages" ON inbox_messages;
CREATE POLICY "anon_update_inbox_messages" ON inbox_messages FOR UPDATE
  TO anon, authenticated USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "anon_delete_inbox_messages" ON inbox_messages;
CREATE POLICY "anon_delete_inbox_messages" ON inbox_messages FOR DELETE
  TO anon, authenticated USING (true);
