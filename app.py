import os, sqlite3, asyncio
from datetime import datetime, timezone, timedelta
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from telegram import Bot

DB_PATH = os.getenv("DB_PATH", "cloviss.db")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
CHANNEL_URL = os.getenv("CHANNEL_URL", "https://t.me/your_channel")
BOT_USERNAME = os.getenv("BOT_USERNAME", "your_bot")
SECRET_KEY = os.getenv("SECRET_KEY", "change-me")

app = Flask(__name__)
app.secret_key = SECRET_KEY

def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    con = db()
    con.executescript("""
    CREATE TABLE IF NOT EXISTS users(
        telegram_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        last_name TEXT,
        last_key_at TEXT
    );
    CREATE TABLE IF NOT EXISTS keys(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        key TEXT UNIQUE NOT NULL,
        used INTEGER NOT NULL DEFAULT 0,
        used_by INTEGER,
        used_at TEXT
    );
    """)
    con.commit()
    con.close()

def notify(text):
    if not BOT_TOKEN or not ADMIN_ID:
        return
    async def send():
        bot = Bot(BOT_TOKEN)
        await bot.send_message(chat_id=ADMIN_ID, text=text)
        await bot.close()
    try:
        asyncio.run(send())
    except Exception as e:
        print("Telegram notification error:", e)

@app.context_processor
def globals_for_template():
    con = db()
    remaining = con.execute("SELECT COUNT(*) c FROM keys WHERE used=0").fetchone()["c"]
    con.close()
    return {"channel_url": CHANNEL_URL, "remaining": remaining}

@app.route("/")
def index():
    return render_template("index.html", bot_username=BOT_USERNAME)

@app.post("/api/auth")
def auth():
    # The frontend posts Telegram user data after Telegram Login Widget authentication.
    data = request.get_json(force=True)
    tg_id = int(data.get("id", 0))
    if not tg_id:
        return jsonify(ok=False, error="Telegram ID missing"), 400

    # In production, verify Telegram Login Widget hash here.
    # The included frontend is intended to be used with Telegram's official widget.
    con = db()
    con.execute("""
        INSERT INTO users(telegram_id, username, first_name, last_name)
        VALUES(?,?,?,?)
        ON CONFLICT(telegram_id) DO UPDATE SET
          username=excluded.username,
          first_name=excluded.first_name,
          last_name=excluded.last_name
    """, (
        tg_id, data.get("username",""), data.get("first_name",""),
        data.get("last_name","")
    ))
    con.commit()
    con.close()
    session["tg_id"] = tg_id
    return jsonify(ok=True)

@app.post("/api/get-key")
def get_key():
    tg_id = session.get("tg_id")
    if not tg_id:
        return jsonify(ok=False, error="Telegram арқылы кіріңіз"), 401

    con = db()
    user = con.execute("SELECT * FROM users WHERE telegram_id=?", (tg_id,)).fetchone()
    if not user:
        con.close()
        return jsonify(ok=False, error="User not found"), 400

    now = datetime.now(timezone.utc)
    if user["last_key_at"]:
        last = datetime.fromisoformat(user["last_key_at"])
        if now - last < timedelta(days=1):
            next_time = last + timedelta(days=1)
            con.close()
            return jsonify(
                ok=False,
                error="Бүгін key алып қойғансыз.",
                next_key_at=next_time.isoformat()
            ), 429

    row = con.execute("SELECT * FROM keys WHERE used=0 ORDER BY id LIMIT 1").fetchone()
    if not row:
        con.close()
        return jsonify(ok=False, error="Қазір бос key жоқ. Кейінірек қайталап көріңіз."), 404

    stamp = now.isoformat()
    con.execute("UPDATE keys SET used=1, used_by=?, used_at=? WHERE id=?",
                (tg_id, stamp, row["id"]))
    con.execute("UPDATE users SET last_key_at=? WHERE telegram_id=?", (stamp, tg_id))
    con.commit()
    con.close()

    username = user["username"] or "без username"
    display = f"@{username}" if user["username"] else str(tg_id)
    notify(
        "🔑 CLOVISS KEY — NEW KEY\n\n"
        f"👤 User: {display}\n"
        f"🆔 ID: {tg_id}\n"
        f"🔐 Key: {row['key']}\n"
        f"🕐 Time: {now.strftime('%d.%m.%Y %H:%M UTC')}"
    )
    return jsonify(ok=True, key=row["key"])

@app.get("/admin")
def admin():
    if session.get("admin_id") != ADMIN_ID or not ADMIN_ID:
        return redirect(url_for("admin_login"))
    con = db()
    keys = con.execute("""
        SELECT k.*, u.username, u.first_name
        FROM keys k LEFT JOIN users u ON u.telegram_id=k.used_by
        ORDER BY k.id DESC LIMIT 300
    """).fetchall()
    stats = {
        "total": con.execute("SELECT COUNT(*) c FROM keys").fetchone()["c"],
        "remaining": con.execute("SELECT COUNT(*) c FROM keys WHERE used=0").fetchone()["c"],
        "used": con.execute("SELECT COUNT(*) c FROM keys WHERE used=1").fetchone()["c"],
        "users": con.execute("SELECT COUNT(*) c FROM users").fetchone()["c"],
    }
    con.close()
    return render_template("admin.html", keys=keys, stats=stats)

@app.route("/admin/login", methods=["GET","POST"])
def admin_login():
    if request.method == "POST":
        try:
            if int(request.form.get("admin_id","0")) == ADMIN_ID and ADMIN_ID:
                session["admin_id"] = ADMIN_ID
                return redirect(url_for("admin"))
        except ValueError:
            pass
        return render_template("admin_login.html", error="ID дұрыс емес")
    return render_template("admin_login.html")

@app.post("/admin/add-keys")
def add_keys():
    if session.get("admin_id") != ADMIN_ID:
        return jsonify(ok=False), 403
    raw = request.form.get("keys","")
    values = [x.strip() for x in raw.splitlines() if x.strip()]
    con = db()
    added = 0
    for key in values:
        try:
            con.execute("INSERT INTO keys(key) VALUES(?)", (key,))
            added += 1
        except sqlite3.IntegrityError:
            pass
    con.commit()
    con.close()
    return redirect(url_for("admin"))

@app.post("/admin/logout")
def admin_logout():
    session.pop("admin_id", None)
    return redirect(url_for("admin_login"))

init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT","10000")))
