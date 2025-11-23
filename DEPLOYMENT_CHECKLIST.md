# Vercel + Supabase Deployment Checklist

## ✅ Completed (by Claude)
- [x] Fixed critical code issues in `app/main.py`
- [x] Cleaned up `requirements.txt` (removed conflicting dependencies)
- [x] Fixed static file serving for Vercel
- [x] Committed and pushed fixes to GitHub
- [x] Created Supabase database schema file
- [x] Verified GitHub Actions scraper configuration

---

## 🔴 CRITICAL - Must Do Now

### 1. Set Up Supabase Database (5 minutes)

1. **Create Supabase Project** (if not done):
   - Go to https://supabase.com
   - Click "New Project"
   - Name: `flygw-production`
   - Password: **Save this securely!**
   - Region: Choose closest to your users
   - Click "Create new project" and wait 2-3 minutes

2. **Create Database Schema**:
   - In Supabase Dashboard → **SQL Editor** (left sidebar)
   - Click "New query"
   - Open: `C:\Users\dklei\Documents\Frontier\20251119\supabase_schema.sql`
   - Copy entire contents
   - Paste into SQL Editor
   - Click **"Run"** (Ctrl+Enter)
   - Should see: "Success" message

3. **Get Connection Strings**:
   - Go to **Project Settings** (gear icon)
   - Click **"Database"** tab
   - Scroll to **"Connection string"** section

   **For Vercel (DATABASE_URL):**
   - Select **"URI"** tab
   - Copy the string (looks like: `postgresql://postgres:[YOUR-PASSWORD]@db.xxxxx.supabase.co:5432/postgres`)
   - **Replace `[YOUR-PASSWORD]`** with your actual password
   - Save this for Step 2

   **For GitHub Actions (SUPABASE_URL and KEY):**
   - Go to **Project Settings** → **API**
   - Copy **"Project URL"** (looks like: `https://xxxxx.supabase.co`)
   - Copy **"service_role key"** (starts with `eyJ...`)
   - Save these for Step 3

---

### 2. Configure Vercel Environment Variables (2 minutes)

1. Go to https://vercel.com/dashboard
2. Select your project: **20251119**
3. Go to **Settings** → **Environment Variables**
4. Add the following variables:

| Variable Name | Value | Notes |
|--------------|-------|-------|
| `DATABASE_URL` | `postgresql://postgres:[PASSWORD]@db.xxxxx.supabase.co:5432/postgres` | Replace with your actual Supabase connection string |
| `ADMIN_PASSWORD` | `your-secure-password` | (Optional) Change from default "admin" |

5. Click **"Save"**
6. Go to **Deployments** tab
7. Click the three dots (...) on latest deployment → **"Redeploy"**

---

### 3. Configure GitHub Actions Secrets (2 minutes)

For the scraper to work:

1. Go to your GitHub repository: https://github.com/dkleinrodick/20251119
2. Click **Settings** → **Secrets and variables** → **Actions**
3. Click **"New repository secret"**
4. Add these secrets:

| Secret Name | Value | Notes |
|------------|-------|-------|
| `SUPABASE_URL` | `https://xxxxx.supabase.co` | From Supabase → Settings → API |
| `SUPABASE_SERVICE_ROLE_KEY` | `eyJ...` | From Supabase → Settings → API → service_role |

5. Click **"Add secret"** for each

---

## 🟢 Optional - Recommended

### 4. Test the Deployment

1. **Check Vercel Deployment**:
   - Wait 2-3 minutes for redeploy
   - Visit your Vercel URL (check Vercel dashboard)
   - Should see the WildFares homepage
   - Try searching for flights

2. **Test GitHub Actions Scraper**:
   - Go to repository → **Actions** tab
   - Click **"Auto Scraper"** workflow
   - Click **"Run workflow"** dropdown → **"Run workflow"**
   - Watch it run (takes ~5-10 minutes)
   - Check if data appears in Supabase

3. **Verify Database**:
   - In Supabase → **Table Editor**
   - Check tables: `flight_cache`, `route_pairs`, `weather_data`
   - Should see data after scraper runs

---

## 🛠️ Troubleshooting

### If Vercel still shows 500 error:

1. **Check deployment logs**:
   - Vercel Dashboard → Deployments → Click latest deployment
   - Click "View Function Logs"
   - Look for error messages

2. **Common issues**:
   - DATABASE_URL not set or incorrect
   - DATABASE_URL missing password
   - Wrong format (needs `postgresql://` not `postgres://`)

3. **If deployment fails to build**:
   ```bash
   cd C:\Users\dklei\Documents\Frontier\20251119
   pip install -r requirements.txt
   python -m app.main
   ```
   This tests locally before deployment

### If GitHub Actions fails:

1. Check **Actions** tab for error logs
2. Verify secrets are set correctly
3. Check `scraper_cloud/requirements.txt` exists

---

## 📊 What Was Fixed

### Critical Bugs Fixed:
1. ❌ **500 Error**: Removed `apscheduler` logger reference (line 31 in main.py)
2. ❌ **Dependency Conflict**: Removed `psycopg2-binary` (conflicted with `asyncpg`)
3. ❌ **Missing Dependencies**: Removed `aiosqlite` and `apscheduler` (not needed)
4. ❌ **Static Files**: Moved logo to `/public` for Vercel compatibility
5. ❌ **Build Config**: Updated `vercel.json` with proper Python build settings

### Files Modified:
- `app/main.py` - Removed scheduler references
- `requirements.txt` - Cleaned and pinned versions
- `vercel.json` - Added build config and static routes
- `public/logo.svg` - Created for static file serving

---

## 🎯 Expected Results

After completing all steps:

✅ **Vercel**: Site loads at your Vercel URL
✅ **Database**: Flight searches cache in Supabase
✅ **Scraper**: GitHub Actions runs every 4 hours automatically
✅ **Admin Panel**: Accessible at `/admin` with your password

---

## 🆘 Need Help?

If you get stuck:
1. Check Vercel deployment logs
2. Check GitHub Actions logs
3. Verify DATABASE_URL format is correct
4. Make sure Supabase schema was created successfully

The deployment should now work! Once you complete Steps 1-3 above, your app will be live.
