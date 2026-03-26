# Jan-Sunwai · Setup Guide (Phase 1 / SNVC Prototype)

## Prerequisites

| Tool | Purpose | Install |
|------|---------|---------|
| Python 3.12+ | Backend + dashboard | python.org |
| Supabase account | DB + Auth | supabase.com (free) |
| Anthropic API key | Claude Haiku classification | console.anthropic.com |
| Meta Developer account | WhatsApp Business API | developers.facebook.com |
| Railway account | Backend hosting | railway.app ($5/month) |

---

## Step 1 — Supabase

1. Create a new Supabase project.
2. Go to **SQL Editor** → paste and run `scripts/supabase_schema.sql`.
3. Copy your **Project URL**, **anon key**, and **service_role key** from Settings → API.
4. Update the seed row at the bottom of the SQL with your Meta phone_number_id (after Step 3).

---

## Step 2 — Environment

```bash
cp .env.example .env
# Fill in all values in .env
```

---

## Step 3 — WhatsApp Business API

1. Go to [developers.facebook.com](https://developers.facebook.com) → Create App → Business.
2. Add **WhatsApp** product.
3. Generate a **permanent System User token** with `whatsapp_business_messaging` permission.
4. Note your **Phone Number ID** and **App Secret**.
5. Set `WA_VERIFY_TOKEN` to any random string (you'll use this in Step 4).

---

## Step 4 — Deploy Backend to Railway

```bash
# Push your repo to GitHub first, then:
# 1. railway.app → New Project → Deploy from GitHub
# 2. Select the /backend subfolder
# 3. Add all env vars from .env in Railway dashboard
# 4. Copy the Railway public URL (e.g. https://jan-sunwai.up.railway.app)
```

Register the webhook with Meta:
- **Callback URL**: `https://jan-sunwai.up.railway.app/webhook`
- **Verify Token**: same as `WA_VERIFY_TOKEN` in your env
- Subscribe to: `messages`

Update the seed row in Supabase:
```sql
update offices
set wa_phone_number_id = 'YOUR_ACTUAL_PHONE_NUMBER_ID'
where short_code = 'DMO';
```

---

## Step 5 — Run Dashboard Locally

```bash
cd dashboard
pip install -r requirements.txt
SUPABASE_URL=... SUPABASE_ANON_KEY=... DEMO_OFFICE_ID=00000000-0000-0000-0000-000000000001 \
  streamlit run app.py
```

For Streamlit Community Cloud deployment:
- Connect your GitHub repo
- Set secrets in `.streamlit/secrets.toml` format via the Streamlit dashboard

---

## Step 6 — Test the Pipeline

Send a WhatsApp message to your test number. You should see:
1. Webhook POST hits Railway
2. Claude Haiku classifies the message (~1 second)
3. Grievance row appears in Supabase
4. Citizen receives acknowledgement with a Ref ID
5. Dashboard shows the new grievance

---

## Running Tests

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt pytest httpx pytest-asyncio
SUPABASE_URL=https://x.supabase.co SUPABASE_SERVICE_ROLE_KEY=x SUPABASE_ANON_KEY=x \
  ANTHROPIC_API_KEY=x WA_VERIFY_TOKEN=x WA_ACCESS_TOKEN=x \
  WA_PHONE_NUMBER_ID=x WA_APP_SECRET=x \
  python -m pytest tests/ -v
```
