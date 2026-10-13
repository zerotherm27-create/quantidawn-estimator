# Hosting on Vercel + the shared Supabase project

1. **Database (one time).** Supabase dashboard > project QuantiDawn > SQL Editor: paste and run
   `supabase/migrations/20261013_qd_estimator.sql`. It only adds `public.qd_docs`, the `qd_bump` function and a
   private `qd-estimator` bucket; it does not touch the website's tables.
2. **Vercel project.** Import this folder, framework "Flask" (entrypoint `server.py`). Environment variables:
   - `SUPABASE_URL` (project URL), `SUPABASE_SERVICE_KEY` (secret / service_role key, server only),
     `SUPABASE_ANON_KEY` (publishable key)
   - `QD_SECRET_KEY` (a long random string), `QD_BASE_URL=https://estimate.quantidawn.com`,
     `QD_SECURE_COOKIES=1`, `QD_TRUST_PROXY=1`, `QD_REQUIRE_AUTH=1`
3. **Team.** Sign-in uses Supabase Auth, but only emails on the team list get in (the website shares the project):
   `SUPABASE_URL=... SUPABASE_SERVICE_KEY=... ./.venv/bin/python manage.py team add anna@quantidawn.com`
   (the person needs a Supabase Auth account with a password: Authentication > Users > Add user).
4. **Domain.** Vercel > Domains > add `estimate.quantidawn.com`, then add the DNS record Vercel shows.

Notes: PDFs upload straight to Supabase Storage (free plan limit 50 MB per file). Sample client plans are not
deployed (`.vercelignore`). Login throttling is per server instance, so rely on Supabase's own auth rate limits.
