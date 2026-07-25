# FB Automation Backend

FastAPI backend for Facebook Marketplace automation with AI-powered content generation.

## Tech Stack

- **FastAPI** — REST API framework
- **Playwright** — Headless browser automation (Facebook interaction)
- **Supabase** — PostgreSQL database (accounts, listings, tasks, logs)
- **Gemini 3 Flash** — AI listing generation via `emergentintegrations`
- **Uvicorn** — ASGI server

## Setup

```bash
pip install -r requirements.txt
playwright install chromium
```

Copy `.env.example` to `.env` and fill in your keys.

## Running

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

API docs available at `http://localhost:8000/docs`

## API Endpoints

### Accounts — `/api/accounts`

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | List all FB accounts |
| POST | `/` | Add a new FB account |
| GET | `/{id}` | Get account details |
| PATCH | `/{id}` | Update account |
| DELETE | `/{id}` | Remove account |

### Listings — `/api/listings`

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | List listings (filter by account/status) |
| POST | `/` | Create a listing record |
| GET | `/{id}` | Get listing details |
| PATCH | `/{id}` | Update listing |
| DELETE | `/{id}` | Mark listing as deleted |

### Automation — `/api/automation`

| Method | Path | Feature |
|--------|------|---------|
| POST | `/new-account-slow` | New Account Slow Listings |
| POST | `/new-account-slow-v2` | New Account Slow Listings V2 (with warmup) |
| POST | `/ultra-ai-listings` | Ultra AI Listings V2.0 (max 100) |
| POST | `/create-drafts` | Create Only Drafts |
| POST | `/renew-listings` | Renew Listings |
| POST | `/relist-listings` | Relist Listings |
| POST | `/draft-publisher-ai` | Draft Publisher with AI |
| POST | `/delete-all-listings` | Delete All Listings |
| POST | `/draft-publisher` | Draft Publisher |
| POST | `/draft-delete` | Draft Delete |
| POST | `/ads-multiplier` | ADS Multiplier |
| POST | `/warmup` | FB Account Warm UP |
| POST | `/profile-updater` | FB Profile Updater |
| POST | `/get-clicks` | Get Clicks on Marketplace |
| POST | `/open-accounts` | Open FB Accounts |

### Auth — `/api/auth`

| Method | Path | Description |
|--------|------|-------------|
| POST | `/signup` | Create account (email + password) |
| POST | `/login` | Sign in, returns access + refresh tokens |
| POST | `/logout` | Sign out (requires Bearer token) |
| GET | `/me` | Get current user (requires Bearer token) |

### Inbox — `/api/inbox`

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | List inbox messages (filter by account/status) |
| POST | `/read` | Read messages from FB inbox (background task) |
| POST | `/auto-reply` | AI auto-reply to pending messages (background task) |
| GET | `/{id}` | Get a single message |
| POST | `/{id}/reply` | Send a manual reply |
| DELETE | `/{id}` | Delete a message |

### Tasks — `/api/tasks`

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | List tasks (filter by status/type) |
| GET | `/logs/all` | Query all automation logs |
| GET | `/{id}` | Get task status + progress |
| POST | `/{id}/cancel` | Cancel a running task |
| GET | `/{id}/logs` | Get task automation logs |

## Database Schema

- **`fb_accounts`** — Credentials, proxy, cookies, warmup level
- **`listings`** — Marketplace listings (draft/published/deleted/relisted)
- **`tasks`** — Background job tracking with progress (0–100%)
- **`automation_logs`** — Audit trail for all automation actions
- **`inbox_messages`** — FB Marketplace messages with AI reply tracking

## Automation Notes

- All automation features are **non-blocking**: they return a `task_id` immediately and run in the background.
- Poll `GET /api/tasks/{task_id}` for real-time progress.
- Passwords and cookies are stored server-side; the accounts endpoint never returns them.
- `delete-all-listings` and `draft-delete` require `confirm: true` to prevent accidental execution.
- Delays are randomized ± a few seconds around the configured value to appear more human-like.
# fb-backend
# fb-backend
