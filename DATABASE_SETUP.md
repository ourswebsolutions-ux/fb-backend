# Database Setup Guide

## Critical Step Required
The API is returning "Internal Server Error" because the database tables don't exist yet. You must run the SQL migrations in your Supabase database.

## Setup Instructions

### Option 1: Run via Supabase Dashboard (Recommended)

1. **Go to Supabase Dashboard**
   - Navigate to https://supabase.com/dashboard
   - Select your project: `yqsazqjidoecrzmbukxm`

2. **Open SQL Editor**
   - Click on "SQL Editor" in the left sidebar
   - Click "New Query"

3. **Run First Migration**
   - Open file: `supabase/migrations/20260713053408_create_fb_automation_schema.sql`
   - Copy the entire SQL content
   - Paste it into the SQL Editor
   - Click "Run" or press `Ctrl+Enter`

4. **Run Second Migration**
   - Open file: `supabase/migrations/20260714024639_create_inbox_messages_table.sql`
   - Copy the entire SQL content
   - Paste it into the SQL Editor
   - Click "Run" or press `Ctrl+Enter`

5. **Verify Tables Created**
   - Go to "Table Editor" in Supabase Dashboard
   - You should see these tables:
     - `fb_accounts`
     - `listings`
     - `tasks`
     - `automation_logs`
     - `inbox_messages`

### Option 2: Run via Supabase CLI

If you have the Supabase CLI installed:

```bash
# Install Supabase CLI (if not installed)
npm install -g supabase

# Login to Supabase
supabase login

# Link to your project
supabase link --project-ref yqsazqjidoecrzmbukxm

# Run migrations
supabase db push
```

### Option 3: Run via psql (Direct Database Connection)

```bash
# Connect to your database
psql "postgresql://postgres.yqsazqjidoecrzmbukxm:P+L_wrZpXGZ5m8c@aws-0-ap-southeast-2.pooler.supabase.com:5432/postgres"

# Run migration files
\i supabase/migrations/20260713053408_create_fb_automation_schema.sql
\i supabase/migrations/20260714024639_create_inbox_messages_table.sql
```

## Verification

After running migrations, test the API:

```bash
curl http://localhost:8000/health
curl http://localhost:8000/api/listings/
```

Both should return successful responses (empty array for listings is expected).

## Tables Created

### 1. fb_accounts
Stores Facebook account credentials and automation status.

### 2. listings
Stores marketplace listings (drafts and published).

### 3. tasks
Tracks background automation jobs and progress.

### 4. automation_logs
Audit log for all automation actions.

### 5. inbox_messages
Stores Facebook Marketplace messages and AI replies.

## Security Notes

- Row Level Security (RLS) is enabled on all tables
- Policies allow access to `anon` and `authenticated` roles
- This is a single-tenant application with no user isolation
- Service role key is used for server-side operations

## Troubleshooting

### Error: "relation does not exist"
This means migrations haven't been run. Follow the steps above.

### Error: Permission denied
Ensure you're using the service role key or have proper database permissions.

### Tables exist but API still fails
Check that the Supabase URL and keys in `.env` are correct.

## Next Steps

After database setup:
1. Test API endpoints at http://localhost:8000/docs
2. Create your first Facebook account via POST /api/accounts
3. Start creating listings and automation tasks
