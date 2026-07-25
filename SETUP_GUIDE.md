# Facebook Automation - Complete Setup Guide

## Prerequisites
- Windows operating system
- Administrator privileges (for Python installation)
- Internet connection

## Quick Setup (Automated)

### Option 1: Automated Setup Script (Recommended)
Run the automated PowerShell script to handle everything:

```powershell
# Open PowerShell as Administrator
cd c:\Users\fa654\OneDrive\Desktop\facebook-automation\fb-auto-backend
.\setup_python_312.ps1
```

The script will:
- Download and install Python 3.12
- Create virtual environment
- Install all dependencies
- Install Playwright browsers
- Verify installation

### Option 2: Manual Setup
If you prefer manual setup, follow these steps:

## Manual Setup Instructions

### Step 1: Install Python 3.12
1. Download Python 3.12.4 from https://www.python.org/downloads/
2. Run the installer with these options:
   - ✅ Add Python to PATH
   - ✅ Install for all users
   - ❌ Install launcher for all users (optional)

### Step 2: Create Virtual Environment
```bash
cd c:\Users\fa654\OneDrive\Desktop\facebook-automation\fb-auto-backend
py -3.12 -m venv venv
```

### Step 3: Activate Virtual Environment
```bash
.\venv\Scripts\Activate.ps1
```

### Step 4: Install Dependencies
```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### Step 5: Install Playwright Browsers
```bash
playwright install
```

## Starting the Application

### Start Backend
```bash
# Make sure virtual environment is activated
cd c:\Users\fa654\OneDrive\Desktop\facebook-automation\fb-auto-backend
.\venv\Scripts\Activate.ps1
python -m uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

### Start Frontend (New Terminal)
```bash
cd c:\Users\fa654\OneDrive\Desktop\facebook-automation\Facebook-automated
npm run dev:react
```

## Access the Application
- **Frontend**: http://localhost:5173
- **Backend API**: http://localhost:8000
- **API Documentation**: http://localhost:8000/docs

## Troubleshooting

### Python Version Issues
If you see "Python 3.14+ is not compatible with Playwright":
- Ensure you're using Python 3.10-3.13
- Check with: `python --version`
- Reinstall with correct version if needed

### Virtual Environment Issues
If activation fails:
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

### Playwright Browser Issues
If browsers aren't found:
```bash
playwright install --force
```

### Port Already in Use
If port 8000 is busy:
```bash
# Kill existing process
netstat -ano | findstr :8000
taskkill /PID <PID> /F
```

## Testing the Fixes

### Test 1: Inbox Read Functionality
1. Open frontend at http://localhost:5173
2. Navigate to Inbox section
3. Click "Read Inbox" button
4. Check backend logs for enhanced logging output
5. Verify messages are saved to database

### Test 2: Marketplace Listing
1. Navigate to Listings section
2. Create a new listing with images
3. Verify account selection dialog is handled automatically
4. Confirm listing is published successfully

## What Was Fixed

### Issue 1: Inbox Message Detection ✅
- Enhanced logging for debugging
- Multiple selector strategies for thread detection
- Comprehensive diagnostics (HTML dumps, screenshots)
- Improved database error handling

### Issue 2: Marketplace Account Selection ✅
- Automatic account/page selection handling
- Multiple dialog detection patterns
- URL re-navigation on selection issues
- Enhanced error logging

### Issue 3: Python Compatibility ✅
- Version check on startup
- Clear error messages
- Automated setup script
- Comprehensive documentation

## Support
If you encounter issues:
1. Check `PYTHON_3.14_ISSUE.md` for Python compatibility
2. Review backend logs for detailed error messages
3. Ensure all dependencies are installed correctly
4. Verify Playwright browsers are installed

## Next Steps After Setup
1. Test inbox read functionality
2. Test Marketplace listing creation
3. Verify real-time data synchronization
4. Test manual reply functionality
5. Verify auto-reply with AI
