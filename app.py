import os
import asyncio
import uuid
from datetime import datetime, timezone

import psycopg
from psycopg.rows import dict_row

from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from telegram import Bot


# =========================================================
# CONFIG
# =========================================================

DATABASE_URL = os.getenv("DATABASE_URL", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
CHANNEL_URL = os.getenv("CHANNEL_URL", "https://t.me/your_channel")
SECRET_KEY = os.getenv("SECRET_KEY", "change-me")

app = Flask(__name__)
app.secret_key = SECRET_KEY

app.config["PERMANENT_SESSION_LIFETIME"] = 60 * 60 * 24 * 365


# =========================================================
# DATABASE
# =========================================================

def db():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set")

    return psycopg.connect(
        DATABASE_URL,
        row_factory=dict_row
    )


def init_db():
    with db() as con:
        with con.cursor() as cur:

            cur.execute("""
                CREATE TABLE IF NOT EXISTS devices(
                    id BIGSERIAL PRIMARY KEY,
                    device_id TEXT UNIQUE NOT NULL,
                    claimed INTEGER NOT NULL DEFAULT 0,
                    claimed_key TEXT,
                    claimed_at TEXT
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS keys(
                    id BIGSERIAL PRIMARY KEY,
                    key TEXT UNIQUE NOT NULL,
                    used INTEGER NOT NULL DEFAULT 0,
                    used_by TEXT,
                    used_at TEXT
                )
            """)

        con.commit()


# =========================================================
# TELEGRAM NOTIFICATION
# =========================================================

def notify(text):

    if not BOT_TOKEN or not ADMIN_ID:
        return

    async def send():
        bot = Bot(BOT_TOKEN)

        try:
            await bot.send_message(
                chat_id=ADMIN_ID,
                text=text
            )
        finally:
            await bot.close()

    try:
        asyncio.run(send())
    except Exception as e:
        print("Telegram notification error:", e)


# =========================================================
# TEMPLATE GLOBALS
# =========================================================

@app.context_processor
def globals_for_template():

    try:
        with db() as con:
            with con.cursor() as cur:

                cur.execute("""
                    SELECT COUNT(*) AS c
                    FROM keys
                    WHERE used = 0
                """)

                remaining = cur.fetchone()["c"]

    except Exception as e:
        print("Stats error:", e)
        remaining = 0

    return {
        "channel_url": CHANNEL_URL,
        "remaining": remaining
    }


# =========================================================
# HOME
# =========================================================

@app.route("/")
def index():

    if "device_id" not in session:

        session.permanent = True

        session["device_id"] = str(
            uuid.uuid4()
        )

    return render_template("index.html")


# =========================================================
# GET FREE KEY
# =========================================================

@app.post("/api/get-key")
def get_key():

    device_id = session.get("device_id")

    if not device_id:

        session.permanent = True

        device_id = str(uuid.uuid4())

        session["device_id"] = device_id


    try:

        with db() as con:

            with con.cursor() as cur:

                # -----------------------------------------
                # DEVICE
                # -----------------------------------------

                cur.execute("""
                    SELECT *
                    FROM devices
                    WHERE device_id = %s
                """, (device_id,))

                device = cur.fetchone()


                # -----------------------------------------
                # ALREADY CLAIMED
                # -----------------------------------------

                if device and device["claimed"] == 1:

                    return jsonify(
                        ok=False,
                        error="❌ You have already received a key."
                    ), 429


                # -----------------------------------------
                # GET AVAILABLE KEY
                # -----------------------------------------

                cur.execute("""
                    SELECT *
                    FROM keys
                    WHERE used = 0
                    ORDER BY id
                    LIMIT 1
                    FOR UPDATE SKIP LOCKED
                """)

                row = cur.fetchone()


                if not row:

                    return jsonify(
                        ok=False,
                        error=(
                            "❌ Қазір бос key жоқ. "
                            "Кейінірек қайталап көріңіз."
                        )
                    ), 404


                now = datetime.now(timezone.utc)
                stamp = now.isoformat()


                # -----------------------------------------
                # DEVICE CLAIM
                # -----------------------------------------

                if not device:

                    cur.execute("""
                        INSERT INTO devices(
                            device_id,
                            claimed,
                            claimed_key,
                            claimed_at
                        )
                        VALUES(%s,%s,%s,%s)
                    """, (
                        device_id,
                        1,
                        row["key"],
                        stamp
                    ))

                else:

                    cur.execute("""
                        UPDATE devices
                        SET
                            claimed = 1,
                            claimed_key = %s,
                            claimed_at = %s
                        WHERE device_id = %s
                    """, (
                        row["key"],
                        stamp,
                        device_id
                    ))


                # -----------------------------------------
                # USE KEY
                # -----------------------------------------

                cur.execute("""
                    UPDATE keys
                    SET
                        used = 1,
                        used_by = %s,
                        used_at = %s
                    WHERE id = %s
                    AND used = 0
                """, (
                    device_id,
                    stamp,
                    row["id"]
                ))

            con.commit()


        # ---------------------------------------------
        # NOTIFY ADMIN
        # ---------------------------------------------

        notify(
            "🔑 CLOVISS KEY — NEW KEY\n\n"
            f"📱 Device: {device_id}\n"
            f"🔐 Key: {row['key']}\n"
            f"🕐 Time: {now.strftime('%d.%m.%Y %H:%M UTC')}"
        )


        return jsonify(
            ok=True,
            key=row["key"]
        )


    except Exception as e:

        print(
            "GET KEY ERROR:",
            e
        )

        return jsonify(
            ok=False,
            error="Server error. Please try again."
        ), 500


# =========================================================
# ADMIN
# =========================================================

@app.get("/admin")
def admin():

    if (
        session.get("admin_id") != ADMIN_ID
        or not ADMIN_ID
    ):
        return redirect(
            url_for("admin_login")
        )


    try:

        with db() as con:

            with con.cursor() as cur:

                # -----------------------------------------
                # KEYS
                # -----------------------------------------

                cur.execute("""
                    SELECT *
                    FROM keys
                    ORDER BY id DESC
                    LIMIT 300
                """)

                keys = cur.fetchall()


                # -----------------------------------------
                # TOTAL
                # -----------------------------------------

                cur.execute("""
                    SELECT COUNT(*) AS c
                    FROM keys
                """)

                total = cur.fetchone()["c"]


                # -----------------------------------------
                # AVAILABLE
                # -----------------------------------------

                cur.execute("""
                    SELECT COUNT(*) AS c
                    FROM keys
                    WHERE used = 0
                """)

                remaining = cur.fetchone()["c"]


                # -----------------------------------------
                # USED
                # -----------------------------------------

                cur.execute("""
                    SELECT COUNT(*) AS c
                    FROM keys
                    WHERE used = 1
                """)

                used = cur.fetchone()["c"]


                # -----------------------------------------
                # DEVICES
                # -----------------------------------------

                cur.execute("""
                    SELECT COUNT(*) AS c
                    FROM devices
                """)

                devices = cur.fetchone()["c"]


                # -----------------------------------------
                # CLAIMED
                # -----------------------------------------

                cur.execute("""
                    SELECT COUNT(*) AS c
                    FROM devices
                    WHERE claimed = 1
                """)

                claimed = cur.fetchone()["c"]


        stats = {
            "total": total,
            "remaining": remaining,
            "used": used,
            "devices": devices,
            "claimed": claimed
        }


        return render_template(
            "admin.html",
            keys=keys,
            stats=stats
        )


    except Exception as e:

        print(
            "ADMIN ERROR:",
            e
        )

        return "Database error", 500


# =========================================================
# ADMIN LOGIN
# =========================================================

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

            if (
                entered_id == ADMIN_ID
                and ADMIN_ID
            ):

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


# =========================================================
# ADD KEYS
# =========================================================

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


    try:

        with db() as con:

            with con.cursor() as cur:

                for key in values:

                    cur.execute("""
                        INSERT INTO keys(key)
                        VALUES(%s)
                        ON CONFLICT(key)
                        DO NOTHING
                    """, (key,))

            con.commit()


    except Exception as e:

        print(
            "ADD KEYS ERROR:",
            e
        )

        return "Database error", 500


    return redirect(
        url_for("admin")
    )


# =========================================================
# RESET USERS
# =========================================================

@app.post("/admin/reset-users")
def reset_users():

    if session.get("admin_id") != ADMIN_ID:

        return jsonify(
            ok=False
        ), 403


    try:

        with db() as con:

            with con.cursor() as cur:

                cur.execute("""
                    UPDATE devices
                    SET
                        claimed = 0,
                        claimed_key = NULL,
                        claimed_at = NULL
                """)

                count = cur.rowcount

            con.commit()


        notify(
            "🔄 CLOVISS KEY — USERS RESET\n\n"
            f"👥 Reset devices: {count}"
        )


        return redirect(
            url_for("admin")
        )


    except Exception as e:

        print(
            "RESET ERROR:",
            e
        )

        return "Database error", 500


# =========================================================
# LOGOUT
# =========================================================

@app.post("/admin/logout")
def admin_logout():

    session.pop(
        "admin_id",
        None
    )

    return redirect(
        url_for("admin_login")
    )


# =========================================================
# START
# =========================================================

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
