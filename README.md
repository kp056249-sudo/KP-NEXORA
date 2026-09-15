# KP NEXORA

A complete Flask data-intelligence workspace styled like the supplied futuristic KP NEXORA dashboard reference.

## Included
- Landing Home + signed-in Home dashboard
- Dashboard, Analytics, AI Analyst, Data Sources, Data Explorer, Forecasts, Reports
- Team, Activity, Notifications, Billing, Profile, Workspace Settings
- Help, About, Pricing, Contact
- CSV/XLSX/XLS/JSON imports
- Real read-only PostgreSQL connector with protected SELECT/WITH query handling
- Google Sheets public + secure OAuth import
- Google Account Sign-In / Sign-Up via OAuth
- Groq Free AI integration
- Secure password hashing and local session secret
- PostgreSQL and Google Sheets test files

The supplied dashboard image is used as a design reference only; it is NOT used as the page background.


## Free AI setup (Groq)
1. Copy `.env.example` to `.env`.
2. Put your Groq API key in `GROQ_API_KEY`. Never commit `.env`.
3. Keep `GROQ_MODEL=openai/gpt-oss-120b` for the free AI configuration.
4. Run `python app.py` and open the local site.
5. The AI page reports its connection status from `/api/ai/status`.

The free configuration is designed for general AI and dataset analysis. It does not claim guaranteed live web search, so current-information questions are handled honestly rather than presenting stale knowledge as live.
