# Putting the estimator online for your team

You get: a sign-in page for your team, plus expiring download links for clients (no client accounts).
Everything below assumes one small server and a domain name.

## What you need
- A server running Linux with Docker, for example a 2 vCPU / 4 GB machine with 40 GB of disk. Drawings are large
  (10-20 MB each), so allow more disk if you keep many projects.
- A domain or subdomain, for example estimate.quantidawn.com, pointing at the server's IP address (a DNS "A" record).
- Ports 80 and 443 open to the internet. Nothing else needs to be open.

## Steps
1. Copy this project to the server (without the `samples/` folder: it holds real client plans and is left out of the image).
2. `cp .env.example .env`, then set `DOMAIN` and a long random `QD_SECRET_KEY`.
3. `docker compose up -d --build`. Caddy fetches the HTTPS certificate on its own.
4. Create the team logins (a password of 10+ characters):
   `docker compose exec app python manage.py adduser anna`
   Also: `passwd`, `deluser`, `users`. Until the first user exists, nobody can sign in (it fails closed).
5. Open https://your-domain, sign in, upload a plan set.

## Sending a file to a client
Open a project, then **Share link**. Choose what to send, how long the link works (1, 7 or 30 days) and how many
downloads are allowed, then Create link. Copy it, or use **Email it** to open a ready-written email.
The client clicks, sees the file name and size, and downloads. No account, no sign-in.
- The link is shown once. Only a hash is stored, so a lost link means making a new one.
- Revoke any link from the same panel. Expired, revoked and used-up links stop working.
- Anyone who has the link can download that one file, so send it only to the client.

## Keeping it safe and running
- Back up the `data/` folder (uploads, saved work, users, links). Copy it nightly to separate storage.
- Update: copy the new code, then `docker compose up -d --build`.
- Wrong passwords lock a user for 10 minutes after 5 tries. Sessions last 12 hours.
- Logs: `docker compose logs -f app`.

## Limits to know about
- One server, one copy of the app, files on its disk. Fine for a small team; not built for scaling out.
- Two people editing the same project at the same moment: the last change wins.
- Links are emailed by your own email program (the Email button opens a draft). The app does not send email itself.
- The AI features need an Anthropic API key and internet access from the server, and are not connected yet.
