# Manual Account Verification Guide

## Current Issue
The account `ae7af93d-7e9e-496e-a0a8-279d40ff20a6` (Phone: 17208134671) has expired cookies and no password stored, so automatic re-verification is not possible.

## Solution: Manual Verification Through Frontend

### Step 1: Start Frontend (if not running)
```powershell
cd c:\Users\fa654\OneDrive\Desktop\facebook-automation\Facebook-automated
npm run dev:react
```

### Step 2: Access Frontend
Open browser to: http://localhost:5173

### Step 3: Navigate to Accounts Section
1. Go to the Accounts/Settings section in the frontend
2. Find the account with phone number: 17208134671
3. Click the "Verify" button for this account

### Step 4: Complete Verification
1. A browser window will open
2. Enter your Facebook credentials if prompted
3. Complete any 2FA/CAPTCHA challenges
4. Wait for the verification to complete (up to 3 minutes)
5. The browser will close automatically when successful

### Step 5: Test Inbox Functionality
After verification, run the test script:
```powershell
cd c:\Users\fa654\OneDrive\Desktop\facebook-automation\fb-auto-backend
.\venv\Scripts\Activate.ps1
python test_inbox_valid_account.py
```

## Alternative: Add Password to Account for Auto-Re-verification

If you want automatic re-verification to work in the future, you need to add the password to the account:

### Option 1: Through Frontend
1. Go to Accounts section
2. Edit the account (17208134671)
3. Add the Facebook password
4. Save the account

### Option 2: Through API
```python
import requests

account_id = "ae7af93d-7e9e-496e-a0a8-279d40ff20a6"
url = f"http://localhost:8000/api/accounts/{account_id}"
data = {
    "password": "your_facebook_password"  # Replace with actual password
}
requests.patch(url, json=data)
```

## Current Status
- ✅ Python 3.12 installed and working
- ✅ Backend server running on port 8000
- ✅ Enhanced logging implemented
- ✅ Automatic re-verification logic added
- ❌ Account needs manual verification (no password stored)
- ⏳ Awaiting manual verification to test inbox functionality
