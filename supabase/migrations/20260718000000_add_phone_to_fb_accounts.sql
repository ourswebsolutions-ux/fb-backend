/*
# Migration: Add phone column to fb_accounts

- Adds optional `phone` column for accounts that login with phone number
- Makes `email` column optional (some accounts use phone only)

Run this in Supabase SQL Editor.
*/

-- Add phone column
ALTER TABLE fb_accounts
  ADD COLUMN IF NOT EXISTS phone TEXT;

-- Make email optional (some FB accounts use phone number to login)
ALTER TABLE fb_accounts
  ALTER COLUMN email DROP NOT NULL;

-- Add index for phone lookups
CREATE INDEX IF NOT EXISTS idx_fb_accounts_phone ON fb_accounts(phone)
  WHERE phone IS NOT NULL;

-- Add check: at least one of email or phone must be present
ALTER TABLE fb_accounts
  DROP CONSTRAINT IF EXISTS fb_accounts_email_or_phone_check;

ALTER TABLE fb_accounts
  ADD CONSTRAINT fb_accounts_email_or_phone_check
  CHECK (email IS NOT NULL OR phone IS NOT NULL);
