# Manual Setup Steps (No Admin Required)

Since the automated script requires administrator privileges, follow these manual steps:

## Step 1: Download and Install Python 3.12
1. Go to https://www.python.org/downloads/
2. Download Python 3.12.4 (or latest 3.12.x)
3. Run the installer with these options:
   - ✅ "Add Python to PATH" 
   - ✅ "Install for all users"
4. Complete the installation

## Step 2: Open PowerShell and Navigate
```powershell
cd c:\Users\fa654\OneDrive\Desktop\facebook-automation\fb-auto-backend
```

## Step 3: Create Virtual Environment
```powershell
py -3.12 -m venv venv
```

## Step 4: Activate Virtual Environment
```powershell
.\venv\Scripts\Activate.ps1
```

## Step 5: Install Dependencies
```powershell
pip install --upgrade pip
pip install -r requirements.txt
```

## Step 6: Install Playwright Browsers
```powershell
playwright install
```

## Step 7: Start the Application
```powershell
# Start backend
python -m uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

## Step 8: Start Frontend (New Terminal)
```powershell
cd c:\Users\fa654\OneDrive\Desktop\facebook-automation\Facebook-automated
npm run dev:react
```

## Verification
Once both servers are running:
- Backend: http://localhost:8000
- Frontend: http://localhost:5173
- API Docs: http://localhost:8000/docs

## Troubleshooting
If you see "execution policy" errors:
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

Then retry Step 4.
