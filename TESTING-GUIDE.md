# KP NEXORA — Testing Guide

## 1. Start
Double-click `start.bat`. It creates a virtual environment, installs requirements and opens http://127.0.0.1:5000.

## 2. Core test
Register → Dashboard → upload `sample_data/demo_sales.csv` → check Dashboard, Analytics, AI Analyst, Data Explorer and Forecasts.

## 3. Google Sheets
Follow `test_data/google_sheets_test.md`. Public-sheet import needs no OAuth. Private Sheets need Google Cloud OAuth credentials:
GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in `.env`.

## 4. PostgreSQL
Run `test_data/postgresql_test.sql` in a PostgreSQL database you control. Use **Test Connection** first, then paste a connection URL and SELECT query into Data Sources to import rows. The connector uses read-only mode and never stores the connection URL.

## 5. OpenAI (optional)
Set GROQ_API_KEY in `.env`. Keep GROQ_MODEL=`openai/gpt-oss-120b` for the free AI setup.

## 6. Production note
For a public production deployment, use PostgreSQL for KP NEXORA's own application database, HTTPS, a managed secret store, real OAuth/email/payment providers, backups, logging and rate limits. The included local SQLite database is intended for local development/testing.

## Advanced AI mode

Set these in `.env` (never commit the real key):

GROQ_API_KEY=your_key
GROQ_MODEL=openai/gpt-oss-120b
GROQ_WEB_SEARCH=false
OPENAI_REASONING_EFFORT=high
OPENAI_WEB_SEARCH=true
OPENAI_SEARCH_CONTEXT_SIZE=medium

The Advanced AI endpoint uses the OpenAI Responses API. It can answer general questions and, when useful, invoke the hosted web search tool. When a latest dataset exists, its computed analysis is supplied as evidence for dataset questions. Web source metadata is returned to the UI when available.


### Groq Free AI verification
- Set `GROQ_API_KEY` in `.env`.
- Keep `GROQ_MODEL=openai/gpt-oss-120b`.
- Visit `/ai-analyst` after signing in.
- Confirm the status pill says `Advanced AI connected` / `Free mode`.
- Ask a general question, then ask a question about the latest uploaded dataset.
- Do not put the API key in GitHub.
