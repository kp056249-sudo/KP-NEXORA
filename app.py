import os, re, json, secrets, sqlite3, hashlib, urllib.request, urllib.error

from pathlib import Path
from datetime import datetime
from functools import wraps

import pandas as pd
import numpy as np
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

BASE_DIR = Path(__file__).resolve().parent
try:
    from dotenv import load_dotenv

    load_dotenv(BASE_DIR / ".env")
except Exception:
    pass

# Google may return additional identity scopes (openid/profile/email) alongside Sheets.
# OAuthLib can reject these harmless additions unless token-scope relaxation is enabled.
# The callback still verifies that the required Sheets readonly scope was granted.
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

# Local Google OAuth: google-auth-oauthlib enforces HTTPS by default.
# For local browser testing only, explicitly allow HTTP when enabled in .env.
# Never enable this flag on production deployments. Google allows localhost
# redirect URIs for development, while production should use HTTPS.
if os.getenv("KP_NEXORA_LOCAL_HTTP", "true").strip().lower() in {"1", "true", "yes", "on"} and not os.getenv("RENDER"):
    os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", str(BASE_DIR / "uploads")))
UPLOAD_DIR.mkdir(exist_ok=True)
DB_PATH = BASE_DIR / "kp_nexora.db"
SECRET_FILE = BASE_DIR / ".flask_secret"

def load_secret():
    env = os.getenv("FLASK_SECRET_KEY", "").strip()
    if env:
        return env
    if SECRET_FILE.exists():
        return SECRET_FILE.read_text(encoding="utf-8").strip()
    value = secrets.token_urlsafe(48)
    SECRET_FILE.write_text(value, encoding="utf-8")
    return value

app = Flask(__name__)
app.secret_key = load_secret()
app.config.update(
    MAX_CONTENT_LENGTH=50 * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=bool(os.getenv("RENDER")),
)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
ALLOWED = {"csv", "xlsx", "xls", "json"}

class DBConnection:
    """Small compatibility layer: SQLite locally, PostgreSQL in production.
    The application keeps its simple execute/fetchone/fetchall API while the
    DATABASE_URL environment variable selects PostgreSQL.
    """
    def __init__(self):
        self.url = os.getenv("DATABASE_URL", "").strip()
        self.is_postgres = bool(self.url)
        if self.is_postgres:
            import psycopg2
            from psycopg2.extras import DictCursor
            self.con = psycopg2.connect(self.url, connect_timeout=10, cursor_factory=DictCursor)
        else:
            self.con = sqlite3.connect(DB_PATH, timeout=15)
            self.con.row_factory = sqlite3.Row
            self.con.execute("PRAGMA foreign_keys=ON")

    def execute(self, sql, params=()):
        if not self.is_postgres:
            return self.con.execute(sql, params)
        if sql.strip().upper().startswith("PRAGMA"):
            return _NoopCursor()
        sql = sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
        sql = sql.replace(" BLOB", " BYTEA")
        sql = sql.replace("INSERT OR IGNORE", "INSERT")
        sql = sql.replace("?", "%s")
        cur = self.con.cursor()
        cur.execute(sql, params)
        return cur

    def executescript(self, script):
        if not self.is_postgres:
            return self.con.executescript(script)
        statements=[]
        for statement in script.split(';'):
            st=statement.strip()
            if st:
                statements.append(st)
        for st in statements:
            self.execute(st)
        return _NoopCursor()

    def commit(self): self.con.commit()
    def rollback(self): self.con.rollback()
    def close(self): self.con.close()

class _NoopCursor:
    def fetchone(self): return None
    def fetchall(self): return []
    def close(self): pass

def db():
    return DBConnection()

def init_db():
    con = db()
    con.executescript("""
    CREATE TABLE IF NOT EXISTS users(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT NOT NULL, email TEXT UNIQUE NOT NULL,
      password_hash TEXT NOT NULL, company TEXT DEFAULT 'KP NEXORA',
      job_title TEXT DEFAULT 'Data Analyst', created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS datasets(
      id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
      name TEXT NOT NULL, filename TEXT, rows INTEGER, cols INTEGER,
      quality REAL DEFAULT 100, source TEXT DEFAULT 'Upload',
      path TEXT, data_blob BLOB, created_at TEXT NOT NULL,
      FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS activities(
      id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
      title TEXT NOT NULL, detail TEXT, created_at TEXT NOT NULL,
      FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS reports(
      id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
      name TEXT NOT NULL, status TEXT DEFAULT 'Ready', created_at TEXT NOT NULL,
      FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS team_members(
      id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
      name TEXT NOT NULL, email TEXT NOT NULL, role TEXT NOT NULL, status TEXT DEFAULT 'Invited', created_at TEXT NOT NULL,
      FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS notifications(
      id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
      title TEXT NOT NULL, detail TEXT NOT NULL, is_read INTEGER DEFAULT 0, created_at TEXT NOT NULL,
      FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS workspace_settings(
      user_id INTEGER PRIMARY KEY, retention TEXT DEFAULT 'Keep until deleted', notifications INTEGER DEFAULT 1, ai_privacy INTEGER DEFAULT 1,
      FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS contact_messages(
      id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, name TEXT NOT NULL, email TEXT NOT NULL, message TEXT NOT NULL, created_at TEXT NOT NULL,
      FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE SET NULL
    );
    CREATE TABLE IF NOT EXISTS google_tokens(
      user_id INTEGER PRIMARY KEY, token_blob TEXT NOT NULL, updated TEXT NOT NULL,
      FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
    );
    """)
    try:
        if con.is_postgres:
            con.execute("ALTER TABLE datasets ADD COLUMN IF NOT EXISTS data_blob BYTEA")
        else:
            cols = [r[1] for r in con.execute("PRAGMA table_info(datasets)").fetchall()]
            if "data_blob" not in cols:
                con.execute("ALTER TABLE datasets ADD COLUMN data_blob BLOB")
    except Exception:
        pass
    # Backward-compatible Google identity column for Google Sign-In.
    try:
        if con.is_postgres:
            con.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS google_sub TEXT")
            con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_google_sub ON users(google_sub) WHERE google_sub IS NOT NULL")
        else:
            cols = [r[1] for r in con.execute("PRAGMA table_info(users)").fetchall()]
            if "google_sub" not in cols:
                con.execute("ALTER TABLE users ADD COLUMN google_sub TEXT")
            con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_google_sub ON users(google_sub)")
    except Exception:
        pass
    con.commit(); con.close()

init_db()

def current_user():
    if not session.get("user_id"): return None
    con = db()
    row = con.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
    con.close()
    return row

def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not current_user():
            return redirect(url_for("signin", next=request.path))
        return fn(*args, **kwargs)
    return wrapper

def log_activity(title, detail=""):
    u = current_user()
    if not u: return
    con = db()
    con.execute("INSERT INTO activities(user_id,title,detail,created_at) VALUES(?,?,?,?)",
                (u["id"], title, detail, datetime.now().isoformat(timespec="seconds")))
    con.commit(); con.close()

def read_dataframe(path):
    ext = Path(path).suffix.lower()
    if ext == ".csv": return pd.read_csv(path)
    if ext in {".xlsx", ".xls"}: return pd.read_excel(path)
    if ext == ".json":
        try: return pd.read_json(path)
        except ValueError:
            return pd.json_normalize(json.loads(Path(path).read_text(encoding="utf-8")))
    raise ValueError("Unsupported file type")

def analyze(df):
    """Run deterministic local analysis on a pandas DataFrame."""
    if df is None:
        raise ValueError("No dataset was supplied.")
    df = df.copy()
    rows, cols = map(int, df.shape)
    missing = int(df.isna().sum().sum())
    total = max(rows * cols, 1)
    quality = round(max(0, 100 - missing / total * 100), 1)

    # Treat columns that are mostly numeric as numeric even when a CSV parser
    # delivered them as text (for example, numbers containing commas).
    numeric_data = {}
    for col in df.columns:
        converted = pd.to_numeric(
            df[col].astype(str).str.replace(",", "", regex=False).str.strip(),
            errors="coerce",
        )
        non_null = int(df[col].notna().sum())
        usable = int(converted.notna().sum())
        if usable >= 3 and (non_null == 0 or usable / max(non_null, 1) >= 0.70):
            numeric_data[str(col)] = converted
    numeric = pd.DataFrame(numeric_data)

    corr = []
    if numeric.shape[1] >= 2:
        c = numeric.corr()
        names = list(c.columns)
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                value = c.loc[a, b]
                if pd.notna(value):
                    corr.append({"x": a, "y": b, "value": round(float(value), 3)})
        corr.sort(key=lambda x: abs(x["value"]), reverse=True)

    forecast = []
    trend_actual = []
    trend_column = None
    if numeric.shape[1]:
        trend_column = str(numeric.columns[0])
        s = numeric.iloc[:, 0].dropna().reset_index(drop=True)
        if len(s):
            trend_actual = [round(float(v), 2) for v in s.tail(20).tolist()]
        if len(s) >= 3:
            x = np.arange(len(s), dtype=float)
            y = s.to_numpy(dtype=float)
            slope, intercept = np.polyfit(x, y, 1)
            forecast = [round(float(intercept + slope * (len(s) + i)), 2) for i in range(5)]

    statistics = []
    for col in numeric.columns[:10]:
        s = numeric[col].dropna()
        if len(s):
            statistics.append({
                "column": str(col),
                "count": int(len(s)),
                "average": round(float(s.mean()), 2),
                "minimum": round(float(s.min()), 2),
                "maximum": round(float(s.max()), 2),
                "median": round(float(s.median()), 2),
            })

    insights = []
    for item in statistics[:5]:
        insights.append(
            f"{item['column']}: average {item['average']:,.2f}; "
            f"range {item['minimum']:,.2f}–{item['maximum']:,.2f}."
        )
    if corr:
        strongest = corr[0]
        insights.insert(0, f"Strongest relationship: {strongest['x']} ↔ {strongest['y']} ({strongest['value']}).")
    if missing:
        insights.append(f"Data quality: {missing:,} missing cells were found across the dataset.")

    return {
        "rows": rows,
        "cols": cols,
        "quality": quality,
        "missing": missing,
        "columns": list(map(str, df.columns)),
        "numeric_columns": list(map(str, numeric.columns)),
        "correlations": corr[:8],
        "forecast": forecast,
        "trend_actual": trend_actual,
        "trend_column": trend_column,
        "statistics": statistics,
        "insights": insights[:6],
    }

def latest_dataset():
    u = current_user()
    if not u: return None
    con = db()
    row = con.execute("SELECT * FROM datasets WHERE user_id=? ORDER BY id DESC LIMIT 1", (u["id"],)).fetchone()
    con.close()
    return row

def dataframe_from_dataset(ds):
    if not ds:
        return None
    try:
        blob = ds["data_blob"] if "data_blob" in ds.keys() else None
        if blob:
            from io import BytesIO
            raw = bytes(blob)
            name = str(ds["filename"] or "data.csv").lower()
            if name.endswith(".json"):
                return pd.read_json(BytesIO(raw))
            if name.endswith(".xlsx") or name.endswith(".xls"):
                return pd.read_excel(BytesIO(raw))
            return pd.read_csv(BytesIO(raw))
        if ds["path"] and Path(ds["path"]).exists():
            return read_dataframe(ds["path"])
    except Exception:
        return None
    return None

def dataset_analysis(ds):
    df = dataframe_from_dataset(ds)
    if df is None:
        return {"rows":0,"cols":0,"quality":0,"missing":0,"columns":[],"numeric_columns":[],"correlations":[],"forecast":[],"trend_actual":[],"trend_column":None,"statistics":[],"insights":[]}
    try: return analyze(df)
    except Exception: return {"rows":0,"cols":0,"quality":0,"missing":0,"columns":[],"correlations":[],"forecast":[],"insights":[]}

@app.context_processor
def inject():
    u=current_user(); unread=0
    if u:
        con=db(); unread=con.execute("SELECT COUNT(*) FROM notifications WHERE user_id=? AND is_read=0",(u["id"],)).fetchone()[0]; con.close()
    return {"user": u, "year": datetime.now().year, "unread_notifications": unread, "owner_name": _clean_env("KP_NEXORA_OWNER_NAME") or "Krishna Pandey", "owner_title": _clean_env("KP_NEXORA_OWNER_TITLE") or "Founder & Owner", "owner_photo": _clean_env("KP_NEXORA_OWNER_PHOTO") or "/static/images/krishna-pandey.png"}

@app.get("/health")
def health():
    try:
        con = db()
        con.execute("SELECT 1").fetchone()
        con.close()
        return jsonify(status="ok", database="connected")
    except Exception as exc:
        return jsonify(status="error", database="unavailable", detail=str(exc)), 503

@app.get("/robots.txt")
def robots_txt():
    site = os.getenv("KP_NEXORA_SITE_URL", "").strip().rstrip("/")
    sitemap = f"{site}/sitemap.xml" if site else "/sitemap.xml"
    return (f"User-agent: *\nAllow: /\nDisallow: /api/\nDisallow: /dashboard\nDisallow: /analytics\nDisallow: /ai-analyst\nDisallow: /data-sources\nDisallow: /data-explorer\nDisallow: /forecasts\nDisallow: /reports\nDisallow: /team\nDisallow: /activity\nDisallow: /notifications\nDisallow: /billing\nDisallow: /profile\nDisallow: /workspace-settings\nSitemap: {sitemap}\n", 200, {"Content-Type": "text/plain; charset=utf-8"})

@app.get("/sitemap.xml")
def sitemap_xml():
    site = os.getenv("KP_NEXORA_SITE_URL", "").strip().rstrip("/")
    base = site or request.url_root.rstrip("/")
    public_paths = ["/", "/about", "/pricing", "/help", "/contact", "/signin", "/register"]
    urls = "".join(f"<url><loc>{base}{path}</loc></url>" for path in public_paths)
    xml = f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>'
    return xml, 200, {"Content-Type": "application/xml; charset=utf-8"}

@app.get("/manifest.webmanifest")
def manifest_webmanifest():
    return jsonify({
        "name": "KP NEXORA",
        "short_name": "KP NEXORA",
        "description": "AI-powered data analytics, forecasting and reporting workspace.",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#07111f",
        "theme_color": "#07111f",
        "icons": []
    })

@app.route("/")
def home():
    if current_user():
        u=current_user(); con=db()
        activities=con.execute("SELECT * FROM activities WHERE user_id=? ORDER BY id DESC LIMIT 4",(u["id"],)).fetchall()
        metrics={
            "datasets": con.execute("SELECT COUNT(*) FROM datasets WHERE user_id=?",(u["id"],)).fetchone()[0],
            "analyses": con.execute("SELECT COUNT(*) FROM activities WHERE user_id=? AND lower(title) LIKE '%analysis%'",(u["id"],)).fetchone()[0],
            "reports": con.execute("SELECT COUNT(*) FROM reports WHERE user_id=?",(u["id"],)).fetchone()[0],
            "notifications": con.execute("SELECT COUNT(*) FROM notifications WHERE user_id=? AND is_read=0",(u["id"],)).fetchone()[0],
        }
        con.close()
        return render_template("home.html", active="home", analysis=dataset_analysis(latest_dataset()), activities=activities, metrics=metrics)
    return render_template("landing.html", active="home")

@app.route("/signin", methods=["GET","POST"])
def signin():
    if request.method == "POST":
        email = request.form.get("email","").strip().lower()
        password = request.form.get("password","")
        con = db(); u = con.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone(); con.close()
        if u and check_password_hash(u["password_hash"], password):
            session["user_id"] = u["id"]; log_activity("Signed in", "New secure session started")
            return redirect(request.args.get("next") or url_for("home"))
        flash("Invalid email or password.", "error")
    return render_template("auth.html", mode="signin")

@app.route("/register", methods=["GET","POST"])
def register():
    con = None
    if request.method == "POST":
        name=request.form.get("name","").strip(); email=request.form.get("email","").strip().lower()
        password=request.form.get("password","")
        if not name or not email or len(password)<6:
            flash("Enter a name, valid email and password of at least 6 characters.", "error")
        else:
            try:
                con=db()
                con.execute("INSERT INTO users(name,email,password_hash,created_at) VALUES(?,?,?,?)",
                            (name,email,generate_password_hash(password),datetime.now().isoformat(timespec="seconds")))
                uid=con.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()["id"]
                now=datetime.now().isoformat(timespec="seconds")
                con.execute("INSERT INTO workspace_settings(user_id) VALUES(?)", (uid,))
                con.execute("INSERT INTO notifications(user_id,title,detail,created_at) VALUES(?,?,?,?)", (uid,"Welcome to KP NEXORA","Your workspace is ready for data analysis.",now))
                con.commit(); con.close()
                session["user_id"]=uid; log_activity("Workspace created","Welcome to KP NEXORA")
                return redirect(url_for("home"))
            except Exception as e:
                if "unique" in str(e).lower() or "duplicate" in str(e).lower():
                    flash("That email is already registered.", "error")
                else:
                    if con is not None:
                        try: con.rollback()
                        except Exception: pass
                    flash(f"Registration failed: {e}", "error")
    return render_template("auth.html", mode="register")

@app.route("/logout")
def logout():
    session.clear(); return redirect(url_for("signin"))

@app.route("/dashboard")
@login_required
def dashboard():
    ds=latest_dataset(); a=dataset_analysis(ds)
    return render_template("dashboard.html", active="dashboard", analysis=a, dataset=ds)

@app.route("/analytics")
@login_required
def analytics():
    ds=latest_dataset(); return render_template("analytics.html", active="analytics", analysis=dataset_analysis(ds), dataset=ds)

@app.route("/ai-analyst")
@login_required
def ai_analyst():
    ds=latest_dataset(); return render_template("ai.html", active="ai", analysis=dataset_analysis(ds), dataset=ds)

@app.route("/data-sources")
@login_required
def data_sources(): return render_template("data_sources.html", active="sources")

@app.route("/data-explorer")
@login_required
def data_explorer():
    ds=latest_dataset()
    rows=[]
    if ds:
        try:
            frame=dataframe_from_dataset(ds)
            rows=frame.head(100).fillna("").to_dict(orient="records") if frame is not None else []
        except Exception: pass
    return render_template("data_explorer.html", active="explorer", rows=rows, dataset=ds)

@app.route("/forecasts")
@login_required
def forecasts():
    ds=latest_dataset()
    return render_template("forecasts.html", active="forecasts", analysis=dataset_analysis(ds), dataset=ds)

@app.route("/reports")
@login_required
def reports():
    u=current_user(); con=db()
    items=con.execute("SELECT * FROM reports WHERE user_id=? ORDER BY id DESC", (u["id"],)).fetchall(); con.close()
    return render_template("reports.html", active="reports", reports=items)

@app.get("/reports/<int:report_id>/download")
@login_required
def download_report(report_id):
    u=current_user(); con=db()
    report=con.execute("SELECT * FROM reports WHERE id=? AND user_id=?", (report_id,u["id"])).fetchone()
    con.close()
    if not report:
        return "Report not found.", 404
    a=dataset_analysis(latest_dataset())
    rows_html="".join(f"<tr><td>{s['column']}</td><td>{s['count']}</td><td>{s['average']:.2f}</td><td>{s['minimum']:.2f}</td><td>{s['maximum']:.2f}</td><td>{s['median']:.2f}</td></tr>" for s in a.get("statistics",[]))
    insights_html="".join(f"<li>{i}</li>" for i in a.get("insights",[]))
    html=f"""<!doctype html><html><head><meta charset='utf-8'><title>{report['name']}</title><style>body{{font-family:Arial,sans-serif;margin:40px;color:#17233b}}table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #ccd6e5;padding:8px;text-align:left}}h1{{margin-bottom:4px}}</style></head><body><h1>KP NEXORA — {report['name']}</h1><p>Generated {report['created_at']}</p><p>Rows: {a['rows']} · Columns: {a['cols']} · Quality: {a['quality']}% · Missing cells: {a['missing']}</p><h2>Insights</h2><ul>{insights_html or '<li>No insights available.</li>'}</ul><h2>Statistics</h2><table><tr><th>Field</th><th>Count</th><th>Average</th><th>Min</th><th>Max</th><th>Median</th></tr>{rows_html}</table></body></html>"""
    from flask import Response
    return Response(html, mimetype="text/html", headers={"Content-Disposition": f"attachment; filename=kp-nexora-report-{report_id}.html"})

@app.route("/team", methods=["GET","POST"])
@login_required
def team():
    u=current_user(); con=db()
    if request.method=="POST":
        name=request.form.get("name","").strip(); email=request.form.get("email","").strip().lower(); role=request.form.get("role","Member").strip() or "Member"
        if not name or not email or "@" not in email:
            flash("Enter a valid member name and email.","error")
        else:
            con.execute("INSERT INTO team_members(user_id,name,email,role,status,created_at) VALUES(?,?,?,?,?,?)",(u["id"],name,email,role,"Invited",datetime.now().isoformat(timespec="seconds")))
            con.commit(); log_activity("Team invitation created",f"{name} · {email}")
            flash(f"Invitation created for {name}.","success")
    members=con.execute("SELECT * FROM team_members WHERE user_id=? ORDER BY id DESC",(u["id"],)).fetchall(); con.close()
    return render_template("team.html", active="team", members=members)

@app.route("/activity")
@login_required
def activity():
    u=current_user(); con=db()
    items=con.execute("SELECT * FROM activities WHERE user_id=? ORDER BY id DESC LIMIT 50",(u["id"],)).fetchall(); con.close()
    return render_template("activity.html", active="activity", activities=items)

@app.route("/notifications", methods=["GET","POST"])
@login_required
def notifications():
    u=current_user(); con=db()
    if request.method=="POST":
        con.execute("UPDATE notifications SET is_read=1 WHERE user_id=?",(u["id"],)); con.commit(); log_activity("Notifications marked read","All workspace notifications")
    items=con.execute("SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC",(u["id"],)).fetchall(); con.close()
    return render_template("notifications.html", active="notifications", notifications=items)

@app.route("/billing")
@login_required
def billing(): return render_template("billing.html", active="billing")

@app.route("/profile", methods=["GET","POST"])
@login_required
def profile():
    u=current_user()
    if request.method=="POST":
        name=request.form.get("name","").strip(); job=request.form.get("job_title","").strip()
        if not name: flash("Name cannot be empty.","error")
        else:
            con=db(); con.execute("UPDATE users SET name=?,job_title=? WHERE id=?",(name,job,u["id"])); con.commit(); con.close(); log_activity("Profile updated",f"Name: {name}"); flash("Profile updated successfully.","success"); return redirect(url_for("profile"))
    return render_template("profile.html", active="profile", owner_photo=_clean_env("KP_NEXORA_OWNER_PHOTO") or "/static/images/krishna-pandey.png")

def cleanup_expired_datasets(user_id, retention):
    days = {"7 days": 7, "30 days": 30}.get(retention)
    if not days:
        return 0
    from datetime import timedelta
    cutoff = datetime.now() - timedelta(days=days)
    con = db()
    rows = con.execute("SELECT id,path FROM datasets WHERE user_id=? AND created_at < ?", (user_id, cutoff.isoformat(timespec="seconds"))).fetchall()
    for row in rows:
        try:
            if row["path"]:
                Path(row["path"]).unlink(missing_ok=True)
        except Exception:
            pass
    if rows:
        con.execute("DELETE FROM datasets WHERE user_id=? AND created_at < ?", (user_id, cutoff.isoformat(timespec="seconds")))
        con.commit()
    con.close()
    return len(rows)

@app.route("/workspace-settings", methods=["GET","POST"])
@login_required
def workspace_settings():
    u=current_user(); con=db()
    if request.method=="POST":
        retention=request.form.get("retention","Keep until deleted")
        notifications=1 if request.form.get("notifications") else 0
        ai_privacy=1 if request.form.get("ai_privacy") else 0
        con.execute("INSERT INTO workspace_settings(user_id,retention,notifications,ai_privacy) VALUES(?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET retention=excluded.retention,notifications=excluded.notifications,ai_privacy=excluded.ai_privacy",(u["id"],retention,notifications,ai_privacy)); con.commit(); expired=cleanup_expired_datasets(u["id"], retention); log_activity("Workspace settings updated",retention); flash(f"Workspace settings saved. {expired} expired dataset(s) removed." if expired else "Workspace settings saved.","success")
    settings=con.execute("SELECT * FROM workspace_settings WHERE user_id=?",(u["id"],)).fetchone()
    if not settings:
        con.execute("INSERT INTO workspace_settings(user_id) VALUES(?)",(u["id"],)); con.commit(); settings=con.execute("SELECT * FROM workspace_settings WHERE user_id=?",(u["id"],)).fetchone()
    con.close(); return render_template("settings.html", active="settings", settings=settings)

@app.route("/help")
def help_page(): return render_template("help.html", active="help")

@app.route("/about")
def about(): return render_template("about.html", active="about")

@app.route("/pricing")
def pricing(): return render_template("pricing.html", active="pricing")

@app.route("/contact", methods=["GET","POST"])
def contact():
    if request.method=="POST":
        name=request.form.get("name","").strip(); email=request.form.get("email","").strip().lower(); message=request.form.get("message","").strip()
        if not name or "@" not in email or not message: flash("Please complete all contact fields.","error")
        else:
            con=db(); con.execute("INSERT INTO contact_messages(user_id,name,email,message,created_at) VALUES(?,?,?,?,?)",(session.get("user_id"),name,email,message,datetime.now().isoformat(timespec="seconds"))); con.commit(); con.close(); flash("Message received. It has been saved to the support queue.","success"); return redirect(url_for("contact"))
    return render_template("contact.html", active="contact")

@app.route("/api/upload", methods=["POST"])
@login_required
def upload():
    f=request.files.get("file")
    if not f or not f.filename: return jsonify(ok=False,error="Choose a file first."),400
    ext=Path(f.filename).suffix.lower().lstrip(".")
    if ext not in ALLOWED: return jsonify(ok=False,error="Only CSV, XLSX, XLS and JSON are supported."),400
    safe=secure_filename(f.filename)
    path=UPLOAD_DIR / f"{secrets.token_hex(6)}_{safe}"
    f.save(path)
    try: df=read_dataframe(path)
    except Exception as e: path.unlink(missing_ok=True); return jsonify(ok=False,error=str(e)),400
    if len(df)>100000: path.unlink(missing_ok=True); return jsonify(ok=False,error="Maximum 100,000 rows per import."),400
    a=analyze(df); u=current_user(); con=db()
    blob=path.read_bytes()
    con.execute("INSERT INTO datasets(user_id,name,filename,rows,cols,quality,source,path,data_blob,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (u["id"],Path(safe).stem,safe,a["rows"],a["cols"],a["quality"],"Upload",str(path),blob,datetime.now().isoformat(timespec="seconds")))
    con.commit(); con.close(); log_activity("Dataset uploaded",f"{safe} · {a['rows']:,} rows"); log_activity("Analysis completed",f"{a['cols']} columns · quality {a['quality']}%")
    return jsonify(ok=True,analysis=a)

def save_external_df(df,name,source):
    if len(df)>100000: raise ValueError("Maximum 100,000 rows per import.")
    filename=secure_filename(name or "import.csv") or "import.csv"
    path=UPLOAD_DIR / f"{secrets.token_hex(6)}_{filename}.csv"
    df.to_csv(path,index=False); a=analyze(df); u=current_user(); con=db()
    blob=df.to_csv(index=False).encode("utf-8")
    con.execute("INSERT INTO datasets(user_id,name,filename,rows,cols,quality,source,path,data_blob,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (u["id"],Path(filename).stem,filename,a["rows"],a["cols"],a["quality"],source,str(path),blob,datetime.now().isoformat(timespec="seconds")))
    con.commit(); con.close(); log_activity(f"{source} imported",f"{filename} · {a['rows']:,} rows"); log_activity("Analysis completed",f"{source} · {a['cols']} columns")
    return a

def parse_google_sheet_url(sheet_url):
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", sheet_url)
    if not m:
        raise ValueError("Paste a valid Google Sheets URL.")
    gid_match = re.search(r"(?:#|[?&])gid=(\d+)", sheet_url)
    return m.group(1), (gid_match.group(1) if gid_match else None)

def google_oauth_configured():
    client_id, client_secret = _google_client_values()
    return bool(client_id and client_secret)

def _token_cipher():
    from cryptography.fernet import Fernet
    import base64
    key = base64.urlsafe_b64encode(hashlib.sha256(app.secret_key.encode("utf-8")).digest())
    return Fernet(key)

def store_google_token(user_id, token_data):
    blob = _token_cipher().encrypt(json.dumps(token_data).encode("utf-8")).decode("utf-8")
    con=db()
    con.execute("INSERT INTO google_tokens(user_id,token_blob,updated) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET token_blob=excluded.token_blob,updated=excluded.updated",
                (user_id,blob,datetime.now().isoformat(timespec="seconds")))
    con.commit(); con.close()

def load_google_token(user_id):
    con=db(); row=con.execute("SELECT token_blob FROM google_tokens WHERE user_id=?",(user_id,)).fetchone(); con.close()
    if not row: return None
    try:
        return json.loads(_token_cipher().decrypt(row["token_blob"].encode("utf-8")).decode("utf-8"))
    except Exception:
        return None

def _clean_env(name):
    value = os.getenv(name, "").strip()
    # Accept values copied into .env with optional surrounding quotes.
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"\"", "'"}:
        value = value[1:-1].strip()
    return value

def _google_client_values():
    client_id = _clean_env("GOOGLE_CLIENT_ID")
    client_secret = _clean_env("GOOGLE_CLIENT_SECRET")
    # Optional Google OAuth JSON fallback. This is useful when the downloaded
    # Web application credentials file is placed beside app.py.
    for filename in ("google_client_secret.json", "credentials.json", "client_secret.json"):
        if client_id and client_secret:
            break
        candidate = BASE_DIR / filename
        if not candidate.exists():
            continue
        try:
            data = json.loads(candidate.read_text(encoding="utf-8"))
            web = data.get("web") or data.get("installed") or {}
            client_id = client_id or str(web.get("client_id") or "").strip()
            client_secret = client_secret or str(web.get("client_secret") or "").strip()
        except Exception:
            continue
    return client_id, client_secret

def _google_login_configured():
    client_id, client_secret = _google_client_values()
    return bool(client_id and client_secret)

def _google_login_client_config():
    client_id, client_secret = _google_client_values()
    return {"web": {
        "client_id": client_id,
        "client_secret": client_secret,
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": [url_for("google_login_callback", _external=True)],
    }}

def _finish_google_login(google_sub, email, name):
    email=(email or "").strip().lower()
    name=(name or "Google User").strip() or "Google User"
    if not google_sub or not email:
        raise ValueError("Google did not return a verified account identity.")
    con=db()
    row=con.execute("SELECT * FROM users WHERE google_sub=?", (google_sub,)).fetchone()
    if not row:
        row=con.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    if row:
        uid=row["id"]
        try:
            con.execute("UPDATE users SET google_sub=? WHERE id=?", (google_sub, uid))
        except Exception:
            pass
    else:
        now=datetime.now().isoformat(timespec="seconds")
        con.execute("INSERT INTO users(name,email,password_hash,created_at,google_sub) VALUES(?,?,?,?,?)",
                     (name,email,generate_password_hash(secrets.token_urlsafe(32)),now,google_sub))
        uid=con.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()["id"]
        con.execute("INSERT INTO workspace_settings(user_id) VALUES(?)", (uid,))
        con.execute("INSERT INTO notifications(user_id,title,detail,created_at) VALUES(?,?,?,?)",
                    (uid,"Welcome to KP NEXORA","Your Google account is connected and your workspace is ready.",now))
    con.commit(); con.close()
    session.clear(); session["user_id"]=uid
    log_activity("Signed in with Google", "Google account authentication completed")
    return uid

@app.get("/api/google/status")
@login_required
def google_status():
    client_id, secret = _google_client_values()
    def fingerprint(value):
        return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12] if value else ""
    return jsonify(
        ok=True,
        configured=bool(client_id and secret),
        client_id_present=bool(client_id),
        client_id_format=client_id.endswith(".apps.googleusercontent.com"),
        client_id_fingerprint=fingerprint(client_id),
        credential_source=("env" if _clean_env("GOOGLE_CLIENT_ID") and _clean_env("GOOGLE_CLIENT_SECRET") else "json-file" if client_id else "missing"),
        login_callback=url_for("google_login_callback", _external=True),
        sheets_callback=url_for("google_oauth_callback", _external=True),
    )

@app.get("/oauth/setup")
def oauth_setup():
    client_id, secret = _google_client_values()
    return render_template("oauth_setup.html", client_id_present=bool(client_id), client_id_format=client_id.endswith(".apps.googleusercontent.com"), login_callback=url_for("google_login_callback", _external=True), sheets_callback=url_for("google_oauth_callback", _external=True), owner_name=_clean_env("KP_NEXORA_OWNER_NAME") or "Krishna Pandey", owner_title=_clean_env("KP_NEXORA_OWNER_TITLE") or "Founder & Owner")

@app.get("/oauth/diagnostics")
def oauth_diagnostics():
    client_id, secret = _google_client_values()
    return jsonify({
        "ok": True,
        "local_http_allowed": os.getenv("OAUTHLIB_INSECURE_TRANSPORT") == "1",
        "kp_nexora_local_http": os.getenv("KP_NEXORA_LOCAL_HTTP", ""),
        "google_client_ready": bool(client_id and secret),
        "client_id_format": client_id.endswith(".apps.googleusercontent.com"),
        "login_callback": url_for("google_login_callback", _external=True),
        "sheets_callback": url_for("google_oauth_callback", _external=True),
        "note": "Local HTTP OAuth is for development only. Use HTTPS in production."
    })

@app.get("/setup-check")
def setup_check():
    client_id, secret = _google_client_values()
    flask_key = _clean_env("FLASK_SECRET_KEY") or (SECRET_FILE.read_text(encoding="utf-8").strip() if SECRET_FILE.exists() else "")
    db_url = _clean_env("DATABASE_URL")
    masked = (client_id[:8] + "…" + client_id[-24:]) if len(client_id) > 36 else client_id
    return jsonify({
        "ok": True,
        "flask_secret_ready": bool(flask_key),
        "database_mode": "PostgreSQL" if db_url else "SQLite (local)",
        "groq_key_ready": bool(_clean_env("GROQ_API_KEY")),
        "groq_model": _clean_env("GROQ_MODEL") or "openai/gpt-oss-120b",
        "google_client_ready": bool(client_id),
        "google_secret_ready": bool(secret),
        "google_client_id_format": client_id.endswith(".apps.googleusercontent.com"),
        "google_client_id_masked": masked,
        "google_credential_source": "environment" if _clean_env("GOOGLE_CLIENT_ID") and _clean_env("GOOGLE_CLIENT_SECRET") else ("JSON file" if client_id else "missing"),
        "google_login_callback": url_for("google_login_callback", _external=True),
        "google_sheets_callback": url_for("google_oauth_callback", _external=True),
        "google_fix": "If Google shows Error 401 invalid_client, the Client ID/Secret must come from the same Google Cloud Web application OAuth client. Re-copy them or place the downloaded Web application JSON beside app.py."
    })

@app.get("/auth/google")
@app.get("/auth/google/login")
def google_login_start():
    if current_user():
        return redirect(url_for("home"))
    if not _google_login_configured():
        flash("Google Login is not configured. Add GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET.", "error")
        return redirect(url_for("signin"))
    try:
        from google_auth_oauthlib.flow import Flow
        flow=Flow.from_client_config(_google_login_client_config(), scopes=["openid","https://www.googleapis.com/auth/userinfo.email","https://www.googleapis.com/auth/userinfo.profile"])
        flow.redirect_uri=url_for("google_login_callback", _external=True)
        auth_url,state=flow.authorization_url(access_type="offline", include_granted_scopes="true", prompt="select_account")
        session["google_login_state"]=state
        session["google_login_code_verifier"]=getattr(flow,"code_verifier",None)
        session["google_login_next"]=request.args.get("next") or url_for("home")
        return redirect(auth_url)
    except Exception as exc:
        flash(f"Google Login setup error: {exc}", "error")
        return redirect(url_for("signin"))

@app.get("/auth/google/callback")
def google_login_callback():
    if not _google_login_configured():
        flash("Google Login is not configured.", "error")
        return redirect(url_for("signin"))
    try:
        from google_auth_oauthlib.flow import Flow
        from google.oauth2 import id_token
        from google.auth.transport import requests as google_requests
        state=session.get("google_login_state")
        if not state or request.args.get("state") != state:
            raise ValueError("Google login security state did not match. Please try again.")
        flow=Flow.from_client_config(_google_login_client_config(), scopes=["openid","https://www.googleapis.com/auth/userinfo.email","https://www.googleapis.com/auth/userinfo.profile"], state=state)
        flow.redirect_uri=url_for("google_login_callback", _external=True)
        if session.get("google_login_code_verifier"):
            flow.code_verifier=session["google_login_code_verifier"]
        flow.fetch_token(authorization_response=request.url)
        if not flow.credentials.id_token:
            raise ValueError("Google did not return an ID token.")
        info=id_token.verify_oauth2_token(flow.credentials.id_token, google_requests.Request(), _google_client_values()[0])
        if not info.get("email_verified"):
            raise ValueError("The Google email address is not verified.")
        _finish_google_login(info.get("sub"), info.get("email"), info.get("name"))
        target=session.pop("google_login_next", url_for("home"))
        session.pop("google_login_state",None); session.pop("google_login_code_verifier",None)
        return redirect(target if str(target).startswith("/") else url_for("home"))
    except Exception as exc:
        session.pop("google_login_state",None); session.pop("google_login_code_verifier",None); session.pop("google_login_next",None)
        flash(f"Google Login failed: {exc}", "error")
        return redirect(url_for("signin"))

@app.get("/oauth/google/start")
@login_required
def google_oauth_start():
    if not google_oauth_configured():
        flash("Google OAuth is not configured. Add GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET first.","error")
        return redirect(url_for("data_sources"))
    try:
        from google_auth_oauthlib.flow import Flow
        config={"web":{"client_id":_google_client_values()[0],"client_secret":_google_client_values()[1],"auth_uri":"https://accounts.google.com/o/oauth2/auth","token_uri":"https://oauth2.googleapis.com/token","redirect_uris":[url_for("google_oauth_callback",_external=True)]}}
        flow=Flow.from_client_config(config,scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])
        flow.redirect_uri=url_for("google_oauth_callback",_external=True)
        # Request a fresh Sheets grant instead of accumulating previously granted identity scopes.
        auth_url,state=flow.authorization_url(access_type="offline",include_granted_scopes="false",prompt="consent")
        session["google_oauth_state"]=state
        session["google_oauth_code_verifier"]=getattr(flow,"code_verifier",None)
        return redirect(auth_url)
    except Exception as exc:
        flash(f"Google OAuth setup error: {exc}","error")
        return redirect(url_for("data_sources"))

@app.get("/oauth/google/callback")
@login_required
def google_oauth_callback():
    if request.args.get("error"):
        err=request.args.get("error")
        if err == "access_denied":
            flash("Google blocked this Sheets authorization because this OAuth app is still in Testing. Add the Google account you are using to Google Cloud → Google Auth Platform → Audience → Test users, then try again.", "error")
        else:
            flash(f"Google Sheets authorization was not granted: {err}", "error")
        return redirect(url_for("data_sources"))
    if not google_oauth_configured(): return redirect(url_for("data_sources"))
    try:
        from google_auth_oauthlib.flow import Flow
        config={"web":{"client_id":_google_client_values()[0],"client_secret":_google_client_values()[1],"auth_uri":"https://accounts.google.com/o/oauth2/auth","token_uri":"https://oauth2.googleapis.com/token","redirect_uris":[url_for("google_oauth_callback",_external=True)]}}
        flow=Flow.from_client_config(config,scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"],state=session.get("google_oauth_state"))
        flow.redirect_uri=url_for("google_oauth_callback",_external=True)
        if session.get("google_oauth_code_verifier"): flow.code_verifier=session["google_oauth_code_verifier"]
        flow.fetch_token(authorization_response=request.url)
        creds=flow.credentials
        required_scope="https://www.googleapis.com/auth/spreadsheets.readonly"
        if required_scope not in set(creds.scopes or []):
            raise RuntimeError("Google did not grant the required Google Sheets readonly permission.")
        if not creds.refresh_token:
            raise RuntimeError("Google did not return a refresh token. Reconnect Google Sheets and approve access again.")
        store_google_token(session["user_id"],{"token":creds.token,"refresh_token":creds.refresh_token,"token_uri":creds.token_uri,"client_id":creds.client_id,"client_secret":creds.client_secret,"scopes":creds.scopes})
        session.pop("google_oauth_state",None); session.pop("google_oauth_code_verifier",None)
        flash("Google Sheets connected. You can now import a private Sheet URL.","success")
    except Exception as exc:
        detail=str(exc)
        low=detail.lower()
        if "invalid_client" in low or "client not found" in low:
            detail="Google rejected the OAuth client. Use the SAME Web application Client ID + Secret, and keep this exact Sheets callback registered: " + url_for("google_oauth_callback", _external=True)
        elif "redirect_uri_mismatch" in low:
            detail="Google rejected the callback URL. Add the exact Sheets callback shown in Data Sources → Google Setup to Authorized redirect URIs."
        elif "admin_policy_enforced" in low:
            detail="Your Google Workspace administrator blocked this OAuth scope. Use a personal/test account or ask the Workspace admin to allow the Sheets scope."
        elif "access_denied" in low or "not verified" in low:
            detail="Google blocked this testing app. In Google Cloud → Google Auth Platform → Audience → Test users, add the EXACT Google account you are using, then retry."
        elif "insecure_transport" in low:
            detail="Local OAuth HTTP is not enabled. Start the app with start.bat from the new ZIP; it enables local HTTP OAuth only for development."
        flash(f"Google OAuth failed: {detail}","error")
    return redirect(url_for("data_sources"))

@app.route("/api/connect/google/private", methods=["POST"])
@login_required
def google_private():
    sheet_url=request.form.get("sheet_url","").strip()
    token_data=load_google_token(session["user_id"])
    if not token_data:
        return jsonify(ok=False,error="Connect Google first using the Google OAuth button."),401
    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        from google.auth.transport.requests import Request as GoogleRequest
        spreadsheet_id,gid=parse_google_sheet_url(sheet_url)
        creds=Credentials(**{k:v for k,v in token_data.items() if v is not None})
        if creds.expired and creds.refresh_token:
            creds.refresh(GoogleRequest())
            store_google_token(session["user_id"], {"token":creds.token,"refresh_token":creds.refresh_token,"token_uri":creds.token_uri,"client_id":creds.client_id,"client_secret":creds.client_secret,"scopes":creds.scopes})
        if not creds.valid and creds.refresh_token:
            creds.refresh(GoogleRequest())
            store_google_token(session["user_id"], {"token":creds.token,"refresh_token":creds.refresh_token,"token_uri":creds.token_uri,"client_id":creds.client_id,"client_secret":creds.client_secret,"scopes":creds.scopes})
        service=build("sheets","v4",credentials=creds,cache_discovery=False)
        meta=service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
        target=None
        for item in meta.get("sheets",[]):
            props=item.get("properties",{})
            if gid is None or str(props.get("sheetId"))==str(gid):
                target=props; break
        if not target: return jsonify(ok=False,error="Requested sheet tab was not found."),400
        title=target.get("title")
        values=service.spreadsheets().values().get(spreadsheetId=spreadsheet_id,range=title).execute().get("values",[])
        if not values: return jsonify(ok=False,error="The selected Google Sheet is empty."),400
        df=pd.DataFrame(values[1:],columns=values[0]).dropna(how="all")
        a=save_external_df(df,f"Google Sheets - {title}","Google Sheets")
        return jsonify(ok=True,analysis=a)
    except Exception as exc:
        detail=str(exc)
        low=detail.lower()
        if "401" in low or "unauthorized" in low or "invalid credentials" in low or "invalid_grant" in low:
            # A stale/revoked Google token cannot be repaired by retrying the Sheet URL.
            # Remove only the saved Google token so the user can perform a clean OAuth grant.
            try:
                con=db(); con.execute("DELETE FROM google_tokens WHERE user_id=?",(session["user_id"],)); con.commit(); con.close()
            except Exception:
                pass
            return jsonify(ok=False,error="Google authorization expired or was revoked. Click Connect Google again, approve Google Sheets access, then import the private Sheet URL again."),401
        return jsonify(ok=False,error=f"Private Google Sheets import failed: {exc}"),400

def _public_google_csv_url(sheet_url):
    """Convert common Google Sheets published/public URLs into a CSV endpoint."""
    from urllib.parse import urlparse, parse_qs, urlencode
    raw = (sheet_url or "").strip()
    if not raw:
        raise ValueError("Paste a Google Sheets public or published URL.")
    parsed = urlparse(raw)
    host = (parsed.netloc or "").lower()
    if host not in {"docs.google.com", "www.docs.google.com"}:
        raise ValueError("The URL must be a docs.google.com Google Sheets URL.")
    path = parsed.path
    query = parse_qs(parsed.query)

    # Normal sheet URL: /spreadsheets/d/<id>/edit (or similar).
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)(?:/|$)", path)
    if m and "/e/" not in path:
        sheet_id = m.group(1)
        gid = (query.get("gid") or [None])[0]
        params = {"format": "csv"}
        if gid and str(gid).isdigit():
            params["gid"] = str(gid)
        return f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?{urlencode(params)}"

    # Published-to-web URL: /spreadsheets/d/e/<published-id>/pub or /pubhtml.
    m = re.search(r"/spreadsheets/d/e/([a-zA-Z0-9_-]+)/(?:pub|pubhtml)(?:/|$)", path)
    if m:
        published_id = m.group(1)
        gid = (query.get("gid") or [None])[0]
        params = {"output": "csv", "single": "true"}
        if gid and str(gid).isdigit():
            params["gid"] = str(gid)
        return f"https://docs.google.com/spreadsheets/d/e/{published_id}/pub?{urlencode(params)}"

    # Some Google UI variants expose the published id as ?id=... on pubhtml.
    if path.endswith("/pubhtml") and query.get("id"):
        published_id = query["id"][0]
        gid = (query.get("gid") or [None])[0]
        params = {"output": "csv", "single": "true"}
        if gid and str(gid).isdigit():
            params["gid"] = str(gid)
        return f"https://docs.google.com/spreadsheets/d/e/{published_id}/pub?{urlencode(params)}"

    raise ValueError("Unsupported Google Sheets URL. Use the URL from File → Share → Publish to web, or a normal /spreadsheets/d/... URL.")

@app.route("/api/connect/google/public", methods=["POST"])
@login_required
def google_public():
    import urllib.request
    url=request.form.get("sheet_url","").strip()
    try:
        export = _public_google_csv_url(url)
        req = urllib.request.Request(export, headers={"User-Agent": "KP-NEXORA/1.0"})
        raw=urllib.request.urlopen(req,timeout=20).read()
        from io import BytesIO
        df=pd.read_csv(BytesIO(raw))
        if df.empty and len(df.columns) == 0:
            raise ValueError("The published Google Sheet returned no tabular data.")
        a=save_external_df(df,"google_sheet_import","Google Sheets")
        return jsonify(ok=True,analysis=a)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            msg="Google returned 404. Make sure the Sheet is still published to the web and paste the complete published URL (the one ending in /pub or /pubhtml)."
        elif e.code in {401,403}:
            msg="Google did not allow the public export. Re-publish the Sheet to the web and use the generated published URL; do not use a private /edit URL here."
        else:
            msg=f"Google returned HTTP {e.code} while reading the published Sheet."
        return jsonify(ok=False,error=msg),400
    except Exception as e:
        return jsonify(ok=False,error=f"Google Sheets import failed: {e}"),400

@app.route("/api/connect/postgres/test", methods=["POST"])
@login_required
def postgres_test():
    try:
        import psycopg2
        conn_url=request.form.get("connection_url","").strip()
        if not conn_url: return jsonify(ok=False,error="PostgreSQL connection URL is required."),400
        conn=psycopg2.connect(conn_url,connect_timeout=10)
        conn.set_session(readonly=True,autocommit=True)
        cur=conn.cursor(); cur.execute("SELECT 1"); cur.fetchone(); cur.close(); conn.close()
        return jsonify(ok=True,message="PostgreSQL connection successful. Read-only test passed.")
    except Exception as e:
        return jsonify(ok=False,error=f"PostgreSQL connection test failed: {e}"),400

@app.route("/api/connect/postgres", methods=["POST"])
@login_required
def postgres_connect():
    try:
        import psycopg2
        conn_url=request.form.get("connection_url","").strip()
        query=request.form.get("query","").strip()
        if not conn_url or not query: return jsonify(ok=False,error="Connection URL and SELECT query are required."),400
        if not re.match(r"^\s*(SELECT|WITH)\b",query,re.I) or ";" in query.rstrip(";"):
            return jsonify(ok=False,error="Only one read-only SELECT/WITH query is allowed."),400
        conn=psycopg2.connect(conn_url,connect_timeout=10)
        conn.set_session(readonly=True,autocommit=False)
        df=pd.read_sql_query(query,conn)
        conn.close()
        a=save_external_df(df,"postgres_import","PostgreSQL")
        return jsonify(ok=True,analysis=a)
    except Exception as e: return jsonify(ok=False,error=f"PostgreSQL connection failed: {e}"),400

@app.route("/api/reports/create", methods=["POST"])
@login_required
def create_report():
    name=request.form.get("name","Business Report").strip() or "Business Report"
    u=current_user(); con=db()
    con.execute("INSERT INTO reports(user_id,name,status,created_at) VALUES(?,?,?,?)",
                (u["id"],name,"Ready",datetime.now().isoformat(timespec="seconds")))
    con.commit(); con.close(); log_activity("Report generated",name)
    return jsonify(ok=True)

def _groq_headers(key):
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json", "User-Agent": "KP-NEXORA/1.0"}

def _groq_models(key):
    req=urllib.request.Request("https://api.groq.com/openai/v1/models", headers=_groq_headers(key), method="GET")
    with urllib.request.urlopen(req, timeout=12) as response:
        data=json.loads(response.read().decode("utf-8", errors="replace"))
    return {str(item.get("id")) for item in (data.get("data") or []) if isinstance(item, dict) and item.get("id")}

def _groq_model_check():
    key=_clean_env("GROQ_API_KEY")
    model=_clean_env("GROQ_MODEL") or "openai/gpt-oss-120b"
    if not key:
        return False, "GROQ_API_KEY is empty. Add your real Groq API key to .env and restart the app."
    try:
        ids=_groq_models(key)
        if ids and model not in ids:
            alternatives=[m for m in ("openai/gpt-oss-120b","openai/gpt-oss-20b","llama-3.3-70b-versatile","llama-3.1-8b-instant") if m in ids]
            if alternatives:
                return True, f"Groq key works. Configured model '{model}' is unavailable; available fallback: {alternatives[0]}. Set GROQ_MODEL={alternatives[0]} for a stable configuration."
            return False, f"Groq key works, but model '{model}' is not available to this API project."
        return True, "Groq API and configured model are reachable."
    except urllib.error.HTTPError as exc:
        raw=exc.read().decode("utf-8", errors="replace")
        try:
            obj=json.loads(raw).get("error") or {}
            detail=obj.get("message") if isinstance(obj,dict) else str(obj)
        except Exception:
            detail=raw or str(exc)
        if exc.code==401: return False, f"Groq rejected the API key (401): {detail}"
        if exc.code==429: return False, f"Groq rate limit reached (429). Wait for the reset and try again: {detail}"
        return False, f"Groq API check failed ({exc.code}): {detail}"
    except Exception as exc:
        return False, f"Could not reach Groq API: {exc}"

@app.get("/api/ai/status")
@login_required
def ai_status():
    model=_clean_env("GROQ_MODEL") or "openai/gpt-oss-120b"
    web_enabled=_clean_env("GROQ_WEB_SEARCH").lower() in {"1","true","yes","on"}
    connected,detail=_groq_model_check()
    return jsonify(ok=True,connected=connected,model=model,web_search=web_enabled,mode="groq-free",detail=detail)

@app.get("/api/ai/diagnostics")
@login_required
def ai_diagnostics():
    connected,detail=_groq_model_check()
    return jsonify(ok=True,connected=connected,model=_clean_env("GROQ_MODEL") or "openai/gpt-oss-120b",detail=detail)

@app.post("/api/ai/test")
@login_required
def ai_test():
    """Perform one tiny real completion so the UI can distinguish a valid key from a merely present key."""
    key=_clean_env("GROQ_API_KEY")
    if not key: return jsonify(ok=False,error="GROQ_API_KEY is empty."),503
    model=_clean_env("GROQ_MODEL") or "openai/gpt-oss-120b"
    payload={"model":model,"messages":[{"role":"user","content":"Reply with exactly: KP NEXORA AI TEST OK"}],"max_completion_tokens":32,"temperature":0}
    try:
        req=urllib.request.Request("https://api.groq.com/openai/v1/chat/completions",data=json.dumps(payload).encode("utf-8"),headers=_groq_headers(key),method="POST")
        with urllib.request.urlopen(req,timeout=20) as response:
            data=json.loads(response.read().decode("utf-8",errors="replace"))
        answer=((data.get("choices") or [{}])[0].get("message") or {}).get("content","").strip()
        if not answer: raise RuntimeError("Groq returned no test answer.")
        return jsonify(ok=True,answer=answer,model=model)
    except urllib.error.HTTPError as exc:
        raw=exc.read().decode("utf-8",errors="replace")
        try:
            obj=json.loads(raw).get("error") or {}; detail=obj.get("message") if isinstance(obj,dict) else str(obj)
        except Exception: detail=raw or str(exc)
        return jsonify(ok=False,error=f"Groq test failed ({exc.code}): {detail}"),502
    except Exception as exc:
        return jsonify(ok=False,error=f"Groq test failed: {exc}"),502

@app.route("/api/ai", methods=["POST"])
@login_required
def ai():
    q=request.json.get("question","").strip() if request.is_json and isinstance(request.json,dict) else ""
    ds=latest_dataset(); a=dataset_analysis(ds)
    if not q: return jsonify(ok=False,error="Ask a question."),400
    key=_clean_env("GROQ_API_KEY")
    if not key: return jsonify(ok=False,error="AI is not connected. Add GROQ_API_KEY to .env and restart start.bat."),503
    try:
        configured_model=_clean_env("GROQ_MODEL") or "openai/gpt-oss-120b"
        model=configured_model
        try:
            ids=_groq_models(key)
            if ids and model not in ids:
                fallbacks=[m for m in ("openai/gpt-oss-120b","openai/gpt-oss-20b","llama-3.3-70b-versatile","llama-3.1-8b-instant") if m in ids]
                if fallbacks: model=fallbacks[0]
                else: raise RuntimeError(f"Configured model '{configured_model}' is not available to this Groq key.")
        except urllib.error.HTTPError:
            # The completion request below will give the authoritative error.
            model=configured_model

        dataset_context={
            "available":bool(ds),"rows":a.get("rows",0),"columns":a.get("cols",0),"quality_percent":a.get("quality",0),
            "missing_cells":a.get("missing",0),"columns_list":a.get("columns",[])[:40],"numeric_columns":a.get("numeric_columns",[])[:30],
            "correlations":a.get("correlations",[])[:20],"forecast":a.get("forecast",[])[:8],"trend_actual":a.get("trend_actual",[])[:30],
            "trend_column":a.get("trend_column"),"statistics":a.get("statistics",[])[:15],"insights":a.get("insights",[])[:15]
        }
        system_prompt=("You are KP NEXORA AI. Answer directly and accurately in the user's language when practical. "
                        "Use dataset context only when relevant and never invent dataset values. "
                        "If the question needs live web data, clearly say live web search is not enabled in this free configuration. "
                        "Keep answers useful and concise.")
        user_prompt=f"DATASET CONTEXT:\n{json.dumps(dataset_context,ensure_ascii=False)}\n\nQUESTION:\n{q}"
        if len(user_prompt)>10000: user_prompt=user_prompt[:10000]+"\n[context shortened]"
        payload={"model":model,"messages":[{"role":"system","content":system_prompt},{"role":"user","content":user_prompt}],"max_completion_tokens":1024,"temperature":0.4}
        req=urllib.request.Request("https://api.groq.com/openai/v1/chat/completions",data=json.dumps(payload).encode("utf-8"),headers=_groq_headers(key),method="POST")
        with urllib.request.urlopen(req,timeout=45) as response:
            raw=response.read().decode("utf-8",errors="replace")
        data=json.loads(raw)
        if data.get("error"):
            err=data.get("error"); raise RuntimeError(err.get("message") if isinstance(err,dict) else str(err))
        choices=data.get("choices") or []
        answer=((choices[0].get("message") if choices else {}) or {}).get("content","").strip()
        if not answer: raise RuntimeError("Groq returned no answer text. Try again.")
        log_activity("Groq AI analysis requested",q[:180])
        return jsonify(ok=True,answer=answer,model=model,mode="groq-free",web_search=False,sources=[])
    except urllib.error.HTTPError as exc:
        raw=exc.read().decode("utf-8",errors="replace")
        try:
            obj=json.loads(raw).get("error") or {}; detail=obj.get("message") if isinstance(obj,dict) else str(obj)
        except Exception: detail=raw or str(exc)
        if exc.code==401: detail=f"Groq API key rejected (401). Create a new active Groq API key and replace GROQ_API_KEY in .env. {detail}"
        elif exc.code==403: detail=f"Groq denied this request (403). Check account/project access. {detail}"
        elif exc.code==429: detail=f"Groq free-tier limit reached (429). Wait for the reset, then retry. {detail}"
        elif exc.code==400: detail=f"Groq rejected the request (400). The app tried to use model '{_clean_env('GROQ_MODEL') or 'openai/gpt-oss-120b'}'. {detail}"
        return jsonify(ok=False,error=detail),502
    except urllib.error.URLError as exc:
        return jsonify(ok=False,error=f"Cannot reach Groq from this PC. Check internet/firewall/DNS. {exc.reason}"),502
    except Exception as exc:
        return jsonify(ok=False,error=f"AI request failed: {exc}"),502

if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.getenv("PORT","5000")),debug=False)
