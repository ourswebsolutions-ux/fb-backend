# FB Automation Backend - Frontend Developer Guide

## Overview
This is a FastAPI backend for Facebook Marketplace automation with AI-powered content generation. The API provides REST endpoints for managing Facebook accounts, listings, automation tasks, and inbox messages.

## ⚠️ Prerequisites
**Database setup is required before the API will work.** See `DATABASE_SETUP.md` for instructions on running the SQL migrations in Supabase.

## Base URL
- **Development:** `http://localhost:8000`
- **Production:** (Configure your production URL)

## API Documentation
Interactive API documentation available at: `http://localhost:8000/docs`

## Authentication
The API uses Supabase Auth for user authentication.

### Endpoints
- `POST /api/auth/signup` - Create new user account
- `POST /api/auth/login` - Sign in and get tokens
- `POST /api/auth/logout` - Sign out (requires Bearer token)
- `GET /api/auth/me` - Get current user info (requires Bearer token)

### Usage
1. Call `/api/auth/login` with email/password to get `access_token` and `refresh_token`
2. Include `access_token` in Authorization header for protected endpoints:
   ```
   Authorization: Bearer <access_token>
   ```

## API Endpoints

### Accounts - `/api/accounts`
Manage Facebook accounts for automation.

| Method | Endpoint | Description | Auth Required |
|--------|----------|-------------|---------------|
| GET | `/` | List all FB accounts | No |
| POST | `/` | Add a new FB account | No |
| GET | `/{id}` | Get account details | No |
| PATCH | `/{id}` | Update account | No |
| DELETE | `/{id}` | Remove account | No |

**Example Request (Create Account):**
```json
POST /api/accounts
{
  "email": "fb_user@example.com",
  "password": "fb_password",
  "proxy": "http://proxy.example.com:8080",
  "notes": "Main account"
}
```

### Listings - `/api/listings`
Manage Facebook Marketplace listings.

| Method | Endpoint | Description | Auth Required |
|--------|----------|-------------|---------------|
| GET | `/` | List listings (filter by account/status) | No |
| POST | `/` | Create a listing record | No |
| GET | `/{id}` | Get listing details | No |
| PATCH | `/{id}` | Update listing | No |
| DELETE | `/{id}` | Mark listing as deleted | No |

**Example Request (Create Listing):**
```json
POST /api/listings
{
  "account_id": "uuid-here",
  "title": "iPhone 13 Pro Max",
  "description": "Excellent condition, 256GB",
  "price": 89900,
  "category": "electronics",
  "condition": "used_good",
  "images": ["https://example.com/image1.jpg"]
}
```

**Note:** Price is in cents (89900 = $899.00)

### Automation - `/api/automation`
Execute Facebook automation tasks. All endpoints return a `task_id` for tracking progress.

| Method | Endpoint | Description | Auth Required |
|--------|----------|-------------|---------------|
| POST | `/new-account-slow` | New Account Slow Listings | No |
| POST | `/new-account-slow-v2` | New Account Slow Listings V2 (with warmup) | No |
| POST | `/ultra-ai-listings` | Ultra AI Listings V2.0 (max 100) | No |
| POST | `/create-drafts` | Create Only Drafts | No |
| POST | `/renew-listings` | Renew Listings | No |
| POST | `/relist-listings` | Relist Listings | No |
| POST | `/draft-publisher-ai` | Draft Publisher with AI | No |
| POST | `/delete-all-listings` | Delete All Listings | No |
| POST | `/draft-publisher` | Draft Publisher | No |
| POST | `/draft-delete` | Draft Delete | No |
| POST | `/ads-multiplier` | ADS Multiplier | No |
| POST | `/warmup` | FB Account Warm UP | No |
| POST | `/profile-updater` | FB Profile Updater | No |
| POST | `/get-clicks` | Get Clicks on Marketplace | No |
| POST | `/open-accounts` | Open FB Accounts | No |

**Example Request (Ultra AI Listings):**
```json
POST /api/automation/ultra-ai-listings
{
  "account_id": "uuid-here",
  "listing_count": 10,
  "product_name": "iPhone 13",
  "category": "electronics",
  "condition": "used_good",
  "price": 69900,
  "images": ["https://example.com/image.jpg"],
  "extra_details": "Unlocked, 128GB"
}
```

**Response:**
```json
{
  "task_id": "uuid-here",
  "message": "Ultra AI listing task started"
}
```

### Tasks - `/api/tasks`
Track automation tasks and view logs.

| Method | Endpoint | Description | Auth Required |
|--------|----------|-------------|---------------|
| GET | `/` | List tasks (filter by status/type) | No |
| GET | `/logs/all` | Query all automation logs | No |
| GET | `/{id}` | Get task status + progress | No |
| POST | `/{id}/cancel` | Cancel a running task | No |
| GET | `/{id}/logs` | Get task automation logs | No |

**Task Status Flow:**
- `pending` → `running` → `completed` or `failed` or `cancelled`

**Example Request (Get Task Status):**
```json
GET /api/tasks/{task_id}
```

**Response:**
```json
{
  "id": "uuid-here",
  "type": "ultra_ai_listings",
  "status": "running",
  "progress": 45,
  "total_steps": 10,
  "completed_steps": 4,
  "error": null,
  "started_at": "2024-01-15T10:00:00Z",
  "finished_at": null
}
```

### Inbox - `/api/inbox`
Manage Facebook Marketplace messages and AI auto-replies.

| Method | Endpoint | Description | Auth Required |
|--------|----------|-------------|---------------|
| GET | `/` | List inbox messages (filter by account/status) | No |
| POST | `/read` | Read messages from FB inbox (background task) | No |
| POST | `/auto-reply` | AI auto-reply to pending messages (background task) | No |
| GET | `/{id}` | Get a single message | No |
| POST | `/{id}/reply` | Send a manual reply | No |
| DELETE | `/{id}` | Delete a message | No |

**Example Request (Auto Reply):**
```json
POST /api/inbox/auto-reply
{
  "account_id": "uuid-here",
  "max_replies": 20,
  "tone": "friendly",
  "custom_instructions": "Be very helpful",
  "delay_seconds": 15
}
```

## Data Models

### Account Status
- `active` - Account is active and ready for automation
- `inactive` - Account is disabled
- `blocked` - Account was blocked by Facebook
- `warming_up` - Account is in warmup phase

### Listing Status
- `draft` - Listing created but not published
- `published` - Listing is live on Facebook
- `deleted` - Listing was deleted
- `relisted` - Listing was relisted

### Task Status
- `pending` - Task is queued
- `running` - Task is currently executing
- `completed` - Task finished successfully
- `failed` - Task failed with error
- `cancelled` - Task was cancelled by user

## Error Handling

All endpoints return standard HTTP status codes:
- `200` - Success
- `400` - Bad Request (invalid input)
- `401` - Unauthorized (invalid/missing token)
- `404` - Resource not found
- `500` - Internal server error

**Error Response Format:**
```json
{
  "detail": "Error message here"
}
```

## Rate Limiting
Currently no rate limiting is implemented. Consider implementing rate limiting for production use.

## CORS Configuration
CORS is enabled for all origins (`allow_origins=["*"]`). For production, restrict to your frontend domain.

## Environment Configuration

The backend requires the following environment variables in `.env`:

```env
SUPABASE_URL=your_supabase_url
SUPABASE_ANON_KEY=your_supabase_anon_key
SUPABASE_SERVICE_ROLE_KEY=your_supabase_service_role_key
EMERGENT_LLM_KEY=your_google_gemini_api_key
```

## Database Schema

### Tables
- **`fb_accounts`** - Facebook account credentials and metadata
- **`listings`** - Marketplace listings with status tracking
- **`tasks`** - Background job tracking with progress
- **`automation_logs`** - Audit trail for automation actions
- **`inbox_messages`** - FB Marketplace messages with AI reply tracking

## AI Features

The backend includes AI-powered features using Google Gemini:
- **Listing Generation** - Auto-generate titles and descriptions
- **Auto-Reply** - AI-powered responses to buyer messages
- **Description Improvement** - Enhance existing listing descriptions

To enable AI features, ensure `EMERGENT_LLM_KEY` is configured in `.env`.

## Integration Steps for Frontend Developer

### 1. Setup Environment
- Get the backend URL from your backend team
- Request API credentials if needed
- Test connection: `GET /health` should return `{"status":"ok"}`

### 2. Implement Authentication
```javascript
// Login
const login = async (email, password) => {
  const response = await fetch('http://localhost:8000/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password })
  });
  const data = await response.json();
  // Store access_token in localStorage/cookies
  return data;
};

// Authenticated Request
const authenticatedRequest = async (endpoint, options = {}) => {
  const token = localStorage.getItem('access_token');
  const response = await fetch(`http://localhost:8000${endpoint}`, {
    ...options,
    headers: {
      ...options.headers,
      'Authorization': `Bearer ${token}`,
      'Content-Type': 'application/json'
    }
  });
  return response.json();
};
```

### 3. Handle Automation Tasks
```javascript
// Start automation
const startAutomation = async (accountId, config) => {
  const response = await fetch('http://localhost:8000/api/automation/ultra-ai-listings', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      account_id: accountId,
      listing_count: 10,
      product_name: "iPhone 13",
      category: "electronics",
      condition: "used_good",
      price: 69900,
      images: []
    })
  });
  const { task_id } = await response.json();
  
  // Poll for progress
  const pollProgress = setInterval(async () => {
    const task = await fetch(`http://localhost:8000/api/tasks/${task_id}`).then(r => r.json());
    updateUI(task.progress);
    
    if (task.status === 'completed' || task.status === 'failed') {
      clearInterval(pollProgress);
    }
  }, 2000);
};
```

### 4. Error Handling
```javascript
const apiCall = async (endpoint, options) => {
  try {
    const response = await fetch(endpoint, options);
    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || 'API Error');
    }
    return await response.json();
  } catch (error) {
    console.error('API Error:', error);
    // Show error to user
  }
};
```

### 5. File Upload for Images
The backend expects image URLs. For image uploads:
1. Upload images to your storage service (S3, Cloudinary, etc.)
2. Pass the URLs to the backend in the `images` array

## Testing

### Health Check
```bash
curl http://localhost:8000/health
```

### Test Authentication
```bash
curl -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"test@example.com","password":"password"}'
```

### Test Create Account
```bash
curl -X POST http://localhost:8000/api/accounts \
  -H "Content-Type: application/json" \
  -d '{"email":"fb@example.com","password":"pass123"}'
```

## Deployment Considerations

### Backend Deployment
1. Set environment variables in production
2. Restrict CORS to your frontend domain
3. Implement rate limiting
4. Use HTTPS
5. Set up proper logging and monitoring

### Database
- Supabase handles database hosting
- Ensure Row Level Security (RLS) is configured
- Backup strategy handled by Supabase

### Security Notes
- Never expose `SUPABASE_SERVICE_ROLE_KEY` to frontend
- Use `SUPABASE_ANON_KEY` for frontend operations
- Implement proper token refresh logic
- Validate all user inputs on both frontend and backend

## Support
For issues or questions:
- Check API docs at `/docs`
- Review automation logs via `/api/tasks/logs/all`
- Contact backend team for server issues

## Common Issues

### Task Not Starting
- Check if account exists and is active
- Verify account credentials are correct
- Check task logs for error details

### AI Features Not Working
- Verify `EMERGENT_LLM_KEY` is set
- Check API key has sufficient credits
- Review task logs for AI-related errors

### Authentication Failures
- Ensure tokens are stored correctly
- Implement token refresh before expiry
- Check `Authorization` header format

## Performance Tips
- Use pagination for listing endpoints
- Poll task status at reasonable intervals (2-5 seconds)
- Cache frequently accessed data
- Implement optimistic UI updates for better UX
