import os
import sqlite3
import asyncio
import uuid

from datetime import datetime, timezone

import psycopg2
import psycopg2.extras

from flask import (
    Flask,
    render_template,
    request,
    jsonify,
    session,
    redirect,
    url_for
)

from telegram import Bot


# =========================================================
# ENV
# =========================================================

DATABASE_URL = os.getenv("DATABASE_URL", "")

# Ескі SQLite база болса, одан PostgreSQL-ға көшіруге пайдаланылады
DB_PATH = os.getenv("DB_PATH", "cloviss.db")

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

CHANNEL_URL = os.getenv(
    "CHANNEL_URL",
    "https://t.me/your_channel"
)

SECRET_KEY = os.getenv(
    "SECRET_KEY",
    "change-me"
)


# =========================================================
# FLASK
# =========================================================

app = Flask(__name__)

app.secret_key = SECRET_KEY

app.config["PERMANENT_SESSION_LIFETIME"] = 60 * 60 * 24 * 365


# =========================================================
# POSTGRESQL CONNECTION
# =========================================================

def db():
    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL environment variable is missing."
        )

    return psycopg2.connect(
        DATABASE_URL,
        cursor_factory=psycopg2.extras.RealDictCursor
    )


# =========================================================
# DATABASE INIT
# =========================================================

def init_db():

    con = db()

    cur = con.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS devices(
            id BIGSERIAL PRIMARY KEY,
            device_id TEXT UNIQUE NOT NULL,
            claimed INTEGER NOT NULL DEFAULT 0,
            claimed_key TEXT,
            claimed_at TEXT
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS keys(
            id BIGSERIAL PRIMARY KEY,
            key TEXT UNIQUE NOT NULL,
            used INTEGER NOT NULL DEFAULT 0,
            used_by TEXT,
            used_at TEXT
        );
    """)

    con.commit()

    cur.close()
    con.close()


# =========================================================
# SQLITE -> POSTGRESQL MIGRATION
# =========================================================

def migrate_sqlite_to_postgres():

    if not os.path.exists(DB_PATH):
        print("SQLite database not found. Migration skipped.")
        return

    print("Checking old SQLite database...")

    try:

        sqlite_con = sqlite3.connect(DB_PATH)
        sqlite_con.row_factory = sqlite3.Row

        sqlite_cur = sqlite_con.cursor()

        postgres_con = db()
        postgres_cur = postgres_con.cursor()

        # -------------------------------------------------
        # MIGRATE KEYS
        # -------------------------------------------------

        try:

            sqlite_cur.execute("""
                SELECT
                    id,
                    key,
                    used,
                    used_by,
                    used_at
                FROM keys
            """)

            old_keys = sqlite_cur.fetchall()

            migrated_keys = 0

            for row in old_keys:

                postgres_cur.execute(
                    """
                    INSERT INTO keys(
                        key,
                        used,
                        used_by,
                        used_at
                    )
                    VALUES(%s,%s,%s,%s)

                    ON CONFLICT(key)
                    DO NOTHING
                    """,
                    (
                        row["key"],
                        row["used"],
                        row["used_by"],
                        row["used_at"]
                    )
                )

                if postgres_cur.rowcount > 0:
                    migrated_keys += 1

            print(
                f"SQLite -> PostgreSQL: "
                f"{migrated_keys} keys migrated."
            )

        except sqlite3.OperationalError:

            print(
                "Old SQLite keys table not found."
            )


        # -------------------------------------------------
        # MIGRATE DEVICES
        # -------------------------------------------------

        try:

            sqlite_cur.execute("""
                SELECT
                    device_id,
                    claimed,
                    claimed_key,
                    claimed_at
                FROM devices
            """)

            old_devices = sqlite_cur.fetchall()

            migrated_devices = 0

            for row in old_devices:

                postgres_cur.execute(
                    """
                    INSERT INTO devices(
                        device_id,
                        claimed,
                        claimed_key,
                        claimed_at
                    )
                    VALUES(%s,%s,%s,%s)

                    ON CONFLICT(device_id)
                    DO UPDATE SET
                        claimed = EXCLUDED.claimed,
                        claimed_key = EXCLUDED.claimed_key,
                        claimed_at = EXCLUDED.claimed_at
                    """,
                    (
                        row["device_id"],
                        row["claimed"],
                        row["claimed_key"],
                        row["claimed_at"]
                    )
                )

                migrated_devices += 1

            print(
                f"SQLite -> PostgreSQL: "
                f"{migrated_devices} devices migrated."
            )

        except sqlite3.OperationalError:

            print(
                "Old SQLite devices table not found."
            )


        postgres_con.commit()

        postgres_cur.close()
        postgres_con.close()

        sqlite_cur.close()
        sqlite_con.close()

        print(
            "SQLite -> PostgreSQL migration completed."
        )

    except Exception as e:

        print(
            "SQLite migration error:",
            e
        )


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

        print(
            "Telegram notification error:",
            e
        )


# =========================================================
# TEMPLATE GLOBALS
# =========================================================

@app.context_processor
def globals_for_template():

    con = db()

    cur = con.cursor()

    cur.execute(
        """
        SELECT COUNT(*) AS c
        FROM keys
        WHERE used = 0
        """
    )

    row = cur.fetchone()

    remaining = row["c"]

    cur.close()
    con.close()

    return {
        "channel_url": CHANNEL_URL,
        "remaining": remaining
    }


# =========================================================
# MAIN PAGE
# =========================================================

@app.route("/")
def index():

    # Browser/device үшін тұрақты ID
    if "device_id" not in session:

        session.permanent = True

        session["device_id"] = str(
            uuid.uuid4()
        )

    return render_template(
        "index.html"
    )


# =========================================================
# GET FREE KEY
# =========================================================

@app.post("/api/get-key")
def get_key():

    # -----------------------------------------------------
    # DEVICE ID
    # -----------------------------------------------------

    device_id = session.get(
        "device_id"
    )

    if not device_id:

        session.permanent = True

        device_id = str(
            uuid.uuid4()
        )

        session["device_id"] = device_id


    con = db()

    cur = con.cursor()


    try:

        # -------------------------------------------------
        # CHECK DEVICE
        # -------------------------------------------------

        cur.execute(
            """
            SELECT *
            FROM devices
            WHERE device_id = %s
            """,
            (device_id,)
        )

        device = cur.fetchone()


        # -------------------------------------------------
        # ALREADY CLAIMED
        # -------------------------------------------------

        if device and device["claimed"] == 1:

            return jsonify(
                ok=False,
                error=(
                    "❌ You have already received a key."
                )
            ), 429


        # -------------------------------------------------
        # GET AVAILABLE KEY
        # -------------------------------------------------

        cur.execute(
            """
            SELECT *
            FROM keys
            WHERE used = 0
            ORDER BY id
            LIMIT 1
            FOR UPDATE SKIP LOCKED
            """
        )

        row = cur.fetchone()


        if not row:

            return jsonify(
                ok=False,
                error=(
                    "❌ Қазір бос key жоқ. "
                    "Кейінірек қайталап көріңіз."
                )
            ), 404


        now = datetime.now(
            timezone.utc
        )

        stamp = now.isoformat()


        # -------------------------------------------------
        # SAVE DEVICE CLAIM
        # -------------------------------------------------

        if not device:

            cur.execute(
                """
                INSERT INTO devices(
                    device_id,
                    claimed,
                    claimed_key,
                    claimed_at
                )
                VALUES(%s,%s,%s,%s)
                """,
                (
                    device_id,
                    1,
                    row["key"],
                    stamp
                )
            )

        else:

            cur.execute(
                """
                UPDATE devices

                SET
                    claimed = 1,
                    claimed_key = %s,
                    claimed_at = %s

                WHERE device_id = %s
                """,
                (
                    row["key"],
                    stamp,
                    device_id
                )
            )


        # -------------------------------------------------
        # MARK KEY AS USED
        # -------------------------------------------------

        cur.execute(
            """
            UPDATE keys

            SET
                used = 1,
                used_by = %s,
                used_at = %s

            WHERE id = %s
            AND used = 0
            """,
            (
                device_id,
                stamp,
                row["id"]
            )
        )


        # -------------------------------------------------
        # COMMIT
        # -------------------------------------------------

        con.commit()


    except Exception as e:

        con.rollback()

        print(
            "GET KEY ERROR:",
            e
        )

        return jsonify(
            ok=False,
            error="Server error. Please try again."
        ), 500


    finally:

        cur.close()
        con.close()


    # -----------------------------------------------------
    # ADMIN NOTIFICATION
    # -----------------------------------------------------

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


# =========================================================
# ADMIN PANEL
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


    con = db()

    cur = con.cursor()


    # -----------------------------------------------------
    # KEYS
    # -----------------------------------------------------

    cur.execute(
        """
        SELECT *
        FROM keys
        ORDER BY id DESC
        LIMIT 300
        """
    )

    keys = cur.fetchall()


    # -----------------------------------------------------
    # STATISTICS
    # -----------------------------------------------------

    cur.execute(
        """
        SELECT COUNT(*) AS c
        FROM keys
        """
    )

    total = cur.fetchone()["c"]


    cur.execute(
        """
        SELECT COUNT(*) AS c
        FROM keys
        WHERE used = 0
        """
    )

    remaining = cur.fetchone()["c"]


    cur.execute(
        """
        SELECT COUNT(*) AS c
        FROM keys
        WHERE used = 1
        """
    )

    used = cur.fetchone()["c"]


    cur.execute(
        """
        SELECT COUNT(*) AS c
        FROM devices
        """
    )

    devices = cur.fetchone()["c"]


    cur.execute(
        """
        SELECT COUNT(*) AS c
        FROM devices
        WHERE claimed = 1
        """
    )

    claimed = cur.fetchone()["c"]


    stats = {

        "total": total,

        "remaining": remaining,

        "used": used,

        "devices": devices,

        "claimed": claimed
    }


    cur.close()
    con.close()


    return render_template(
        "admin.html",
        keys=keys,
        stats=stats
    )


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


    con = db()

    cur = con.cursor()

    added = 0


    try:

        for key in values:

            cur.execute(
                """
                INSERT INTO keys(
                    key
                )
                VALUES(%s)

                ON CONFLICT(key)
                DO NOTHING
                """,
                (key,)
            )

            if cur.rowcount > 0:
                added += 1


        con.commit()


    except Exception as e:

        con.rollback()

        print(
            "ADD KEYS ERROR:",
            e
        )

    finally:

        cur.close()
        con.close()


    return redirect(
        url_for("admin")
    )


# =========================================================
# RESET ALL DEVICES
# =========================================================

@app.post("/admin/reset-users")
def reset_users():

    if session.get("admin_id") != ADMIN_ID:

        return jsonify(
            ok=False
        ), 403


    con = db()

    cur = con.cursor()


    try:

        # Барлық device қайтадан key ала алады
        cur.execute(
            """
            UPDATE devices

            SET
                claimed = 0,
                claimed_key = NULL,
                claimed_at = NULL
            """
        )

        count = cur.rowcount

        con.commit()


    except Exception as e:

        con.rollback()

        print(
            "RESET ERROR:",
            e
        )

        return jsonify(
            ok=False,
            error="Reset error"
        ), 500


    finally:

        cur.close()
        con.close()


    notify(
        "🔄 CLOVISS KEY — USERS RESET\n\n"
        f"👥 Reset devices: {count}"
    )


    return redirect(
        url_for("admin")
    )


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

# Ескі SQLite база бар болса,
# PostgreSQL-ға автоматты көшіреді.
migrate_sqlite_to_postgres()


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
