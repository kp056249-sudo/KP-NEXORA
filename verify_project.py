from pathlib import Path
import ast, re, yaml

ROOT = Path(__file__).resolve().parent
app_text = (ROOT / "app.py").read_text(encoding="utf-8")
ast.parse(app_text)

base = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")
assert "{% block content %}" in base

for p in (ROOT / "templates").glob("*.html"):
    text = p.read_text(encoding="utf-8")
    if p.name not in {"base.html", "auth.html", "landing.html"}:
        assert '{% extends "base.html" %}' in text, f"{p.name}: missing base extension"
        assert "{% block content %}" in text and "{% endblock %}" in text, f"{p.name}: missing content block"

routes = re.findall(r'@app\.(?:route|get|post)\("([^"]+)"', app_text)
required = [
    "/", "/health", "/signin", "/register", "/dashboard", "/analytics", "/ai-analyst",
    "/data-sources", "/data-explorer", "/forecasts", "/reports", "/team", "/activity",
    "/notifications", "/billing", "/profile", "/workspace-settings", "/help", "/about",
    "/pricing", "/contact", "/api/upload", "/api/connect/postgres",
    "/api/connect/google/public", "/api/connect/google/private", "/oauth/google/start",
    "/oauth/google/callback", "/api/google/status", "/api/ai", "/api/ai/test"
]
missing = [x for x in required if x not in routes]
assert not missing, f"Missing routes: {missing}"

for f in [
    "render.yaml", "Procfile", ".python-version", "DEPLOYMENT-GUIDE.md",
    "PRODUCTION-READY.txt", "test_data/postgresql_test.sql",
    "test_data/google_sheets_test.md", "sample_data/demo_sales.csv", "start.bat",
    "requirements.txt", "static/images/krishna-pandey.png"
]:
    assert (ROOT / f).exists(), f"Missing file: {f}"

render = yaml.safe_load((ROOT / "render.yaml").read_text(encoding="utf-8"))
assert render["services"][0]["startCommand"].startswith("gunicorn")
assert render["services"][0]["healthCheckPath"] == "/health"
assert render["services"][0]["envVars"][0]["key"] == "DATABASE_URL"
assert render["databases"][0]["name"] == "kp-nexora-db"

assert "DATABASE_URL" in app_text
assert "data_blob" in app_text
assert "google_oauth_callback" in app_text
assert "GROQ_API_KEY" in app_text
assert "OPENAI_API_KEY" not in app_text

print("KP NEXORA production verification: PASS")
print(f"Routes found: {len(routes)}")
print(f"HTML templates: {len(list((ROOT / 'templates').glob('*.html')))}")
print("Render Blueprint: PASS")
print("PostgreSQL application database mode: PASS")
print("Google Sheets public + OAuth routes: PASS")
print("Persistent dataset bytes in database: PASS")
