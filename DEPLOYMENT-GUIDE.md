# KP NEXORA — PUBLIC PRODUCTION DEPLOYMENT

This build is prepared for a public Flask deployment on Render with managed PostgreSQL.

## What this gives you

- Public HTTPS URL reachable from phones and laptops.
- Real registration/login sessions.
- PostgreSQL as the application database when `DATABASE_URL` is set.
- User data isolated by account.
- Dataset bytes stored in PostgreSQL so uploaded data does not depend on an ephemeral web-server filesystem.
- Analytics, forecasts, AI Analyst, reports, team, activity, notifications, settings and support routes.
- Health check at `/health`.
- Gunicorn production server.
- Render Blueprint (`render.yaml`) that creates the web service and PostgreSQL database.

## Deploy

1. Create a GitHub account/repository if you do not already have one.
2. Extract this ZIP.
3. Create a new GitHub repository named `kp-nexora`.
4. Upload the **contents of the project folder** to that repository. `app.py` and `render.yaml` must be at the repository root.
5. In Render, choose **New → Blueprint** and connect the GitHub repository.
6. Render reads `render.yaml`, creates the web service and PostgreSQL database, installs requirements and starts Gunicorn.
7. During the first Blueprint sync, enter the real values for `GROQ_API_KEY`, `GOOGLE_CLIENT_ID`, and `GOOGLE_CLIENT_SECRET` if those integrations are wanted. They are intentionally not stored in this ZIP.
8. Wait for `/health` to become healthy. Open the Render URL shown for the web service.

## Google OAuth — Account Login + Google Sheets

This build uses the same Google OAuth client for two separate flows:
- **Google Account Login:** signs users into KP NEXORA with their verified Google email.
- **Google Sheets:** authorizes read-only access to private Google Sheets for import.

After the site has a public HTTPS URL, add BOTH exact callback URLs to the same Google OAuth Web Client:

`https://YOUR-DOMAIN/auth/google/callback`

`https://YOUR-DOMAIN/oauth/google/callback`

For local development, use the exact local origin shown by your Flask server, commonly:

`http://127.0.0.1:5000`

and callbacks:

`http://127.0.0.1:5000/auth/google/callback`

`http://127.0.0.1:5000/oauth/google/callback`

The redirect URI must match exactly. Do not expose the Client Secret in GitHub or in chat.

## Custom domain

After the Render service is live, add your purchased domain in Render's custom-domain settings and follow the DNS records Render provides. HTTPS is then handled by the hosting platform.

## Important production note

The Blueprint intentionally uses paid `starter` web and `basic-256mb` Postgres resources. Render documents that free web services can spin down after inactivity and that free Postgres has time limitations, so the free tier should be treated as testing rather than a serious production setup.

## Local development

Windows: double-click `start.bat`.

Local development uses SQLite automatically when `DATABASE_URL` is not set. Public deployment uses PostgreSQL automatically when Render supplies `DATABASE_URL`.


## Public website / search engines
The included production config is ready for deployment. Set `KP_NEXORA_SITE_URL` to the final HTTPS domain. The app exposes `/robots.txt`, `/sitemap.xml`, and `/manifest.webmanifest` and includes mobile-friendly metadata.

A ZIP file alone cannot make a site appear on every phone or in Google Search. The app must be deployed to a public HTTPS host, and search-engine indexing is controlled by the search engine. A custom domain is recommended for a stable public address.
