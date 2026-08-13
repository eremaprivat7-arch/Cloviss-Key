import os
import sqlite3
import asyncio
import uuid

from datetime import datetime, timezone

from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from telegram import Bot


DB_PATH = os.getenv("DB_PATH", "cloviss.db")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
CHANNEL_URL = os.getenv("CHANNEL_URL", "https://t.me/your_channel")
SECRET_KEY = os.getenv("SECRET_KEY", "change-me")


app = Flask(__name__)
app.secret_key = SECRET_KEY

# Cookie ұзақ сақталуы үшін
app.config["PERMANENT_SESSION_LIFETIME"] = 60 * 60 * 24 * 365


def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db():

    con = db()

    con.executescript("""
    CREATE TABLE IF NOT EXISTS devices(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        device_id TEXT UNIQUE NOT NULL,
        claimed INTEGER NOT NULL DEFAULT 0,
        claimed_key TEXT,
        claimed_at TEXT
    );

    CREATE TABLE IF NOT EXISTS keys(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        key TEXT UNIQUE NOT NULL,
        used INTEGER NOT NULL DEFAULT 0,
        used_by TEXT,
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

        await bot.send_message(
            chat_id=ADMIN_ID,
            text=text
        )

        await bot.close()

    try:
        asyncio.run(send())

    except Exception as e:
        print("Telegram notification error:", e)


@app.context_processor
def globals_for_template():

    con = db()

    remaining = con.execute(
        "SELECT COUNT(*) c FROM keys WHERE used=0"
    ).fetchone()["c"]

    con.close()

    return {
        "channel_url": CHANNEL_URL,
        "remaining": remaining
    }


@app.route("/")
def index():

    # Browser-ге тұрақты device ID береміз
    if "device_id" not in session:

        session.permanent = True

        session["device_id"] = str(uuid.uuid4())

    return render_template("index.html")


@app.post("/api/get-key")
def get_key():

    # Device ID
    device_id = session.get("device_id")

    if not device_id:

        session.permanent = True

        device_id = str(uuid.uuid4())

        session["device_id"] = device_id


    con = db()


    # Device бұрын кірген бе?
    device = con.execute(
        """
        SELECT *
        FROM devices
        WHERE device_id=?
        """,
        (device_id,)
    ).fetchone()


    # Бір рет KEY алған болса
    if device and device["claimed"] == 1:

        con.close()

        return jsonify(
            ok=False,
            error="❌ You have already received a key."
        ), 429


    # Бос KEY іздеу
    row = con.execute(
        """
        SELECT *
        FROM keys
        WHERE used=0
        ORDER BY id
        LIMIT 1
        """
    ).fetchone()


    if not row:

        con.close()

        return jsonify(
            ok=False,
            error="❌ Қазір бос key жоқ. Кейінірек қайталап көріңіз."
        ), 404


    now = datetime.now(timezone.utc)

    stamp = now.isoformat()


    # Device бұрын болмаған болса
    if not device:

        con.execute(
            """
            INSERT INTO devices(
                device_id,
                claimed,
                claimed_key,
                claimed_at
            )
            VALUES(?,?,?,?)
            """,
            (
                device_id,
                1,
                row["key"],
                stamp
            )
        )

    else:

        con.execute(
            """
            UPDATE devices

            SET
                claimed=1,
                claimed_key=?,
                claimed_at=?

            WHERE device_id=?
            """,
            (
                row["key"],
                stamp,
                device_id
            )
        )


    # KEY-ді қолданылған деп белгілеу
    con.execute(
        """
        UPDATE keys

        SET
            used=1,
            used_by=?,
            used_at=?

        WHERE id=?
        """,
        (
            device_id,
            stamp,
            row["id"]
        )
    )


    con.commit()
    con.close()


    # Telegram-ға админге хабарлама
    notify(
        "🔑 CLOVISS KEY — NEW KEY\n\n"

        f"📱 Device: {device_id}\n"

        f"🔐 Key: {row['key']}\n"

        f"🕐 Time: "
        f"{now.strftime('%d.%m.%Y %H:%M UTC')}"
    )


    return jsonify(
        ok=True,
        key=row["key"]
    )


# =========================
# ADMIN
# =========================

@app.get("/admin")
def admin():

    if session.get("admin_id") != ADMIN_ID or not ADMIN_ID:

        return redirect(
            url_for("admin_login")
        )


    con = db()


    keys = con.execute(
        """
        SELECT *
        FROM keys

        ORDER BY id DESC

        LIMIT 300
        """
    ).fetchall()


    stats = {

        "total":
            con.execute(
                "SELECT COUNT(*) c FROM keys"
            ).fetchone()["c"],

        "remaining":
            con.execute(
                "SELECT COUNT(*) c FROM keys WHERE used=0"
            ).fetchone()["c"],

        "used":
            con.execute(
                "SELECT COUNT(*) c FROM keys WHERE used=1"
            ).fetchone()["c"],

        "devices":
            con.execute(
                "SELECT COUNT(*) c FROM devices"
            ).fetchone()["c"],

        "claimed":
            con.execute(
                "SELECT COUNT(*) c FROM devices WHERE claimed=1"
            ).fetchone()["c"]

    }


    con.close()


    return render_template(
        "admin.html",
        keys=keys,
        stats=stats
    )


@app.route(
    "/admin/login",
    methods=["GET", "POST"]
)
def admin_login():

    if request.method == "POST":

        try:

            entered_id = int(
                request.form.get(
                    "admin_id",
                    "0"
                )
            )

            if entered_id == ADMIN_ID and ADMIN_ID:

                session["admin_id"] = ADMIN_ID

                return redirect(
                    url_for("admin")
                )

        except ValueError:

            pass


        return render_template(
            "admin_login.html",
            error="ID дұрыс емес"
        )


    return render_template(
        "admin_login.html"
    )


# =========================
# ADD KEYS
# =========================

@app.post("/admin/add-keys")
def add_keys():

    if session.get("admin_id") != ADMIN_ID:

        return jsonify(
            ok=False
        ), 403


    raw = request.form.get(
        "keys",
        ""
    )


    values = [
        x.strip()
        for x in raw.splitlines()
        if x.strip()
    ]


    con = db()

    added = 0


    for key in values:

        try:

            con.execute(
                "INSERT INTO keys(key) VALUES(?)",
                (key,)
            )

            added += 1

        except sqlite3.IntegrityError:

            pass


    con.commit()
    con.close()


    return redirect(
        url_for("admin")
    )


# =========================
# RESET ALL DEVICES
# =========================

@app.post("/admin/reset-users")
def reset_users():

    if session.get("admin_id") != ADMIN_ID:

        return jsonify(
            ok=False
        ), 403


    con = db()


    # Барлық device қайтадан KEY ала алады
    con.execute(
        """
        UPDATE devices

        SET
            claimed=0,
            claimed_key=NULL,
            claimed_at=NULL
        """
    )


    con.commit()


    count = con.execute(
        "SELECT changes()"
    ).fetchone()[0]


    con.close()


    notify(
        "🔄 CLOVISS KEY — USERS RESET\n\n"
        f"👥 Reset devices: {count}"
    )


    return redirect(
        url_for("admin")
    )


# =========================
# LOGOUT
# =========================

@app.post("/admin/logout")
def admin_logout():

    session.pop(
        "admin_id",
        None
    )

    return redirect(
        url_for("admin_login")
    )


# =========================
# START
# =========================

init_db()


if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=int(
            os.getenv(
                "PORT",
                "10000"
            )
        )
    )
