# Setup for shared use

Running `streamlit run app.py` on one computer keeps data in a local file.
For everyone to share one live system you need three things: a hosted
database, the app deployed, and (optionally) the Google Sheet linked.

## 1. Hosted database (Supabase, free tier)

1. Create a project at supabase.com. Choose a strong database password.
2. Project Settings > Database > Connection string > URI. Use the
   "Session pooler" string. It looks like
   `postgresql://postgres.abcd:[PASSWORD]@aws-0-ap-south-1.pooler.supabase.com:5432/postgres`.
3. That string is your `DATABASE_URL`. The app creates all tables itself on
   first start.

Any Postgres database works; Supabase is suggested because the sheet script
in step 3 uses its REST endpoint.

## 2. Deploy the app (Streamlit Community Cloud)

1. Push this repository to GitHub.
2. At share.streamlit.io choose New app, pick the repository and set the
   main file to `college-timetable/app.py`.
3. Under Advanced settings > Secrets, paste:

   ```toml
   DATABASE_URL = "postgresql://...your string from step 1..."
   ADMIN_EMAIL = "timetable.committee@example.edu"
   ADMIN_PASSWORD = "a long password you will change after first sign-in"
   ```

4. Deploy. Sign in with the admin email and password, then add users under
   Users. When a hosted database is configured the app never offers the
   "create admin" form; the first admin comes only from these secrets.

Local use with the hosted database: set the same three values as environment
variables before `streamlit run app.py`.

## 3. Link the master Google Sheet (optional)

This sends every edit on the sheet to the app within seconds. The app then
applies it, or rejects it if it clashes with an existing booking.

1. In Supabase > SQL Editor, run once so the table is reachable but private:

   ```sql
   alter table sheet_edits enable row level security;
   ```

   (With no policies, only the service key can write to it.)
2. Open the Google Sheet > Extensions > Apps Script. Paste the contents of
   `sheet_script.gs` and save.
3. Project Settings (gear icon) > Script Properties, add:
   - `DB_URL`: your Supabase project URL, e.g. `https://abcd1234.supabase.co`
   - `DB_KEY`: Supabase > Project Settings > API > `service_role` key
4. Triggers (clock icon) > Add Trigger: function `sendEdit`, event source
   "From spreadsheet", event type "On edit". Approve the permissions.
5. Edit a cell in a day tab. In the app, open Data > Sheet edits; the edit
   should be listed as applied or rejected with a reason.

Do the same on the lab sheet if it is a separate file.

Keep the `service_role` key only in Script Properties. Anyone who has it can
write to the database.

If the master is an Excel file rather than a Google Sheet, skip this step:
re-upload the file under Data > Room workbooks whenever it changes. The app
shows what changed before you save.

## 4. Housekeeping

- Back up: Supabase > Database > Backups, or download each view from
  Timetables.
- New semester: upload the new workbooks and inputs, generate, publish.
- Tests: `cd college-timetable && python -m pytest tests`.
