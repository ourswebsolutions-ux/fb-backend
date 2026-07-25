# Frontend Integration Roadmap

This document explains how to connect a frontend application to the FB Automation Backend.

---

## 1. Tech Stack Recommendation

| Layer | Technology | Why |
|-------|-----------|-----|
| Framework | React + Vite | Fast dev server, SPA-friendly |
| UI Library | Tailwind CSS + shadcn/ui | Clean components, consistent design |
| API Client | Native `fetch` or `axios` | Simple, no extra abstraction needed |
| Auth | Supabase JS SDK (`@supabase/supabase-js`) | Handles JWT tokens, session refresh |
| State | React Context + `useState` | Lightweight, no Redux needed |
| Routing | React Router | Standard SPA routing |

---

## 2. Project Structure

```
frontend/
├── src/
│   ├── api/
│   │   └── client.ts          # Base fetch wrapper with auth token injection
│   ├── context/
│   │   └── AuthContext.tsx     # Auth provider (login, logout, session)
│   ├── pages/
│   │   ├── Login.tsx           # Signup + login forms
│   │   ├── Dashboard.tsx       # Overview of accounts + active tasks
│   │   ├── Accounts.tsx        # FB account CRUD
│   │   ├── Listings.tsx        # Listing management
│   │   ├── Automation.tsx      # 15 automation feature buttons
│   │   ├── Inbox.tsx           # Inbox messages + auto-reply
│   │   └── TaskMonitor.tsx     # Task progress polling
│   ├── components/
│   │   ├── Sidebar.tsx
│   │   ├── AccountCard.tsx
│   │   ├── TaskProgress.tsx
│   │   └── ConfirmDialog.tsx
│   └── main.tsx
├── package.json
└── vite.config.ts
```

---

## 3. API Base Configuration

Create a single API client that injects the auth token into every request:

```typescript
// src/api/client.ts

const API_BASE = import.meta.env.VITE_API_URL || "http://localhost:8000";

let authToken: string | null = null;

export function setAuthToken(token: string | null) {
  authToken = token;
}

export async function api<T>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...options.headers,
  };

  if (authToken) {
    headers["Authorization"] = `Bearer ${authToken}`;
  }

  const res = await fetch(`${API_BASE}${path}`, { ...options, headers });

  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(error.detail || "Request failed");
  }

  return res.json();
}
```

---

## 4. Authentication Flow

### 4.1 Login Page

```typescript
// POST /api/auth/login
const response = await api<{ access_token: string; refresh_token: string }>(
  "/api/auth/login",
  {
    method: "POST",
    body: JSON.stringify({ email, password }),
  }
);

// Store tokens
localStorage.setItem("access_token", response.access_token);
localStorage.setItem("refresh_token", response.refresh_token);
setAuthToken(response.access_token);
```

### 4.2 Signup Page

```typescript
// POST /api/auth/signup
await api("/api/auth/signup", {
  method: "POST",
  body: JSON.stringify({ email, password }),
});
// After signup, redirect to login
```

### 4.3 Auth Context Provider

```typescript
// src/context/AuthContext.tsx
import { createContext, useContext, useEffect, useState } from "react";

interface AuthState {
  isLoggedIn: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthState>(null!);

export function AuthProvider({ children }) {
  const [isLoggedIn, setIsLoggedIn] = useState(false);

  useEffect(() => {
    const token = localStorage.getItem("access_token");
    if (token) {
      setAuthToken(token);
      setIsLoggedIn(true);
    }
  }, []);

  const login = async (email: string, password: string) => {
    const res = await api<{ access_token: string }>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    });
    localStorage.setItem("access_token", res.access_token);
    setAuthToken(res.access_token);
    setIsLoggedIn(true);
  };

  const logout = () => {
    localStorage.removeItem("access_token");
    setAuthToken(null);
    setIsLoggedIn(false);
  };

  return (
    <AuthContext.Provider value={{ isLoggedIn, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}
```

### 4.4 Protected Routes

Wrap routes that require auth:

```typescript
function ProtectedRoute({ children }) {
  const { isLoggedIn } = useContext(AuthContext);
  return isLoggedIn ? children : <Navigate to="/login" />;
}
```

---

## 5. Core Feature Integration

### 5.1 FB Account Management

```typescript
// List accounts
const accounts = await api<FBAccount[]>("/api/accounts/");

// Create account
await api("/api/accounts/", {
  method: "POST",
  body: JSON.stringify({ email, password, proxy }),
});

// Update account
await api(`/api/accounts/${id}`, {
  method: "PATCH",
  body: JSON.stringify({ status: "active" }),
});
```

### 5.2 Triggering Automation Features

All 15 automation endpoints follow the same pattern — POST with parameters, get back a `task_id`:

```typescript
// Example: Ultra AI Listings
const { task_id } = await api<{ task_id: string }>("/api/automation/ultra-ai-listings", {
  method: "POST",
  body: JSON.stringify({
    account_id: selectedAccountId,
    listing_count: 50,
    product_name: "iPhone 14 Pro",
    category: "electronics",
    condition: "used_good",
    price: 79900,  // $799.00 in cents
    images: ["https://example.com/img1.jpg"],
    extra_details: "Mint condition, original box included",
  }),
});

// Now poll for progress
const pollProgress = async () => {
  const task = await api<Task>(`/api/tasks/${task_id}`);
  console.log(`${task.progress}% - ${task.completed_steps}/${task.total_steps}`);
  if (task.status === "running") {
    setTimeout(pollProgress, 2000);
  }
};
pollProgress();
```

### 5.3 Task Progress Polling Component

```typescript
function TaskProgress({ taskId }: { taskId: string }) {
  const [task, setTask] = useState<Task | null>(null);

  useEffect(() => {
    const interval = setInterval(async () => {
      const t = await api<Task>(`/api/tasks/${taskId}`);
      setTask(t);
      if (t.status !== "running" && t.status !== "pending") {
        clearInterval(interval);
      }
    }, 2000);
    return () => clearInterval(interval);
  }, [taskId]);

  if (!task) return <div>Starting...</div>;

  return (
    <div>
      <div className="progress-bar" style={{ width: `${task.progress}%` }} />
      <span>{task.status} - {task.completed_steps}/{task.total_steps}</span>
      {task.error && <div className="error">{task.error}</div>}
    </div>
  );
}
```

### 5.4 Inbox Auto-Reply Flow

```typescript
// Step 1: Read inbox messages
const { task_id } = await api("/api/inbox/read", {
  method: "POST",
  body: JSON.stringify({ account_id, max_messages: 50 }),
});
// Wait for task to complete (poll /api/tasks/{task_id})

// Step 2: View pending messages
const messages = await api("/api/inbox/?account_id=...&reply_status=pending");

// Step 3: Trigger auto-reply with AI
const { task_id: replyTaskId } = await api("/api/inbox/auto-reply", {
  method: "POST",
  body: JSON.stringify({
    account_id,
    max_replies: 20,
    tone: "friendly",
    custom_instructions: "Always offer a small discount for quick pickup",
    delay_seconds: 15,
  }),
});

// Step 4: Manual reply for a specific message
await api(`/api/inbox/${messageId}/reply`, {
  method: "POST",
  body: JSON.stringify({ reply_text: "Yes, it's still available!" }),
});
```

---

## 6. Environment Variables

Create a `.env` file in the frontend:

```env
VITE_API_URL=http://localhost:8000
```

For production, set this to your deployed backend URL.

---

## 7. Suggested Page Layout

| Page | Purpose |
|------|---------|
| **Login/Signup** | Auth forms, token storage |
| **Dashboard** | Account overview, active task count, recent logs |
| **Accounts** | Add/edit/delete FB accounts, view warmup level, proxy config |
| **Listings** | Filter by status (draft/published/deleted), view listing details |
| **Automation** | 15 feature cards with config forms, each triggers a background task |
| **Inbox** | Message list, read/unread indicators, auto-reply trigger, manual reply box |
| **Tasks** | All tasks with progress bars, cancel button, per-task log viewer |

---

## 8. Automation Feature Config Forms

Each of the 15 automation features needs a form with its specific parameters. Here's the mapping:

| Feature | Endpoint | Key Form Fields |
|---------|----------|-----------------|
| New Account Slow | `/api/automation/new-account-slow` | account_id, listing_count, delay_seconds, use_ai, product_name, price |
| New Account Slow V2 | `/api/automation/new-account-slow-v2` | Same as above + warmup_before, warmup_steps |
| Ultra AI Listings | `/api/automation/ultra-ai-listings` | account_id, listing_count (max 100), product_name, category, price, extra_details |
| Create Only Drafts | `/api/automation/create-drafts` | account_id, draft_count, title, price, use_ai |
| Renew Listings | `/api/automation/renew-listings` | account_id, max_renew, delay_seconds |
| Relist Listings | `/api/automation/relist-listings` | account_id, max_relist, delay_seconds |
| Draft Publisher AI | `/api/automation/draft-publisher-ai` | account_id, max_publish, improve_with_ai, delay_seconds |
| Delete All Listings | `/api/automation/delete-all-listings` | account_id, status_filter, confirm (checkbox) |
| Draft Publisher | `/api/automation/draft-publisher` | account_id, max_publish, delay_seconds |
| Draft Delete | `/api/automation/draft-delete` | account_id, max_delete, confirm (checkbox) |
| ADS Multiplier | `/api/automation/ads-multiplier` | account_id, multiplier (2-10), delay_seconds |
| FB Warm UP | `/api/automation/warmup` | account_id, duration_minutes, actions_per_minute |
| Profile Updater | `/api/automation/profile-updater` | account_id, name, bio, location, profile_pic_url |
| Get Clicks | `/api/automation/get-clicks` | account_id (returns view counts) |
| Open Accounts | `/api/automation/open-accounts` | account_ids[], action |

---

## 9. Error Handling Pattern

```typescript
async function handleAutomation(feature: string, params: any) {
  try {
    setLoading(true);
    const { task_id } = await api(`/api/automation/${feature}`, {
      method: "POST",
      body: JSON.stringify(params),
    });
    setActiveTaskId(task_id);
  } catch (err) {
    setError(err.message);
  } finally {
    setLoading(false);
  }
}
```

Surface errors visibly — toast notifications or inline error banners. Never silently swallow API failures.

---

## 10. Deployment Notes

| Component | Where |
|-----------|-------|
| FastAPI backend | Deploy to server with Playwright + Chromium installed |
| Frontend | Build with `npm run build`, deploy to Vercel/Netlify/any static host |
| Supabase | Already provisioned, connection via env vars |
| Environment | Set `VITE_API_URL` on the frontend to point to the backend URL |

### CORS

The backend already allows all origins (`*`). For production, restrict to your frontend domain in `main.py`.

### Playwright on Server

The backend needs Chromium installed:
```bash
playwright install chromium
playwright install-deps  # Linux system dependencies
```

---

## 11. Full Request/Response Examples

### Create Account + Trigger Warmup + Run Listings

```typescript
// 1. Create FB account
const account = await api("/api/accounts/", {
  method: "POST",
  body: JSON.stringify({
    email: "fbuser@example.com",
    password: "securepass",
    proxy: "1.2.3.4:8080:user:pass",
  }),
});

// 2. Warm up the account
const { task_id: warmupTaskId } = await api("/api/automation/warmup", {
  method: "POST",
  body: JSON.stringify({
    account_id: account.id,
    duration_minutes: 15,
    actions_per_minute: 3,
  }),
});

// 3. Wait for warmup to finish (poll /api/tasks/{warmupTaskId})

// 4. Run ultra AI listings
const { task_id: listingTaskId } = await api("/api/automation/ultra-ai-listings", {
  method: "POST",
  body: JSON.stringify({
    account_id: account.id,
    listing_count: 30,
    product_name: "Samsung Galaxy S24",
    category: "electronics",
    condition: "new",
    price: 89900,
    extra_details: "Unlocked, sealed box, warranty included",
  }),
});

// 5. Read inbox and auto-reply
const { task_id: readTaskId } = await api("/api/inbox/read", {
  method: "POST",
  body: JSON.stringify({ account_id: account.id, max_messages: 30 }),
});

// After read completes:
const { task_id: replyTaskId } = await api("/api/inbox/auto-reply", {
  method: "POST",
  body: JSON.stringify({
    account_id: account.id,
    max_replies: 20,
    tone: "friendly",
    custom_instructions: "Offer 5% discount for same-day pickup",
  }),
});
```
