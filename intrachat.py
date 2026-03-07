import json
import os
import random
import re
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from functools import wraps

import psutil
import requests
from flask import (
    Flask,
    Response,
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from flask_login import (
    LoginManager,
    current_user,
    login_required,
    login_user,
    logout_user,
)
from flask_socketio import SocketIO, disconnect, emit, join_room
from markupsafe import escape
from sqlalchemy import inspect, or_, text
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

from database import ChatMessage, IPLog, User, ban_log, db

VERSION = "1.0.0"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LANG_DIR = os.path.join(BASE_DIR, "lang")

DEFAULT_CHAT_ROOMS = [
    {"key": "room:general", "icon": "#", "label_key": "room.general"},
    {"key": "room:team", "icon": "T", "label_key": "room.team"},
    {"key": "room:dev", "icon": "D", "label_key": "room.dev"},
    {"key": "room:support", "icon": "S", "label_key": "room.support"},
    {"key": "room:random", "icon": "R", "label_key": "room.random"},
]

AVAILABLE_LANGUAGES = {
    "sk": "Slovencina",
    "en": "English",
    "pl": "Polski",
}

def find_language_file(language_code):
    if not os.path.isdir(LANG_DIR):
        return None

    suffix = f"-{language_code.lower()}.json"
    matches = sorted(
        file_name
        for file_name in os.listdir(LANG_DIR)
        if file_name.lower().endswith(suffix)
    )
    if not matches:
        return None
    return os.path.join(LANG_DIR, matches[0])


def load_translations():
    translations = {}

    for language_code in AVAILABLE_LANGUAGES:
        lang_path = find_language_file(language_code)
        if not lang_path:
            translations[language_code] = {}
            continue

        try:
            with open(lang_path, "r", encoding="utf-8") as lang_file:
                loaded = json.load(lang_file)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Invalid translation file: {lang_path}") from exc

        if not isinstance(loaded, dict):
            raise RuntimeError(f"Translation file must contain a JSON object: {lang_path}")

        translations[language_code] = loaded

    translations.setdefault("en", {})
    return translations


TRANSLATIONS = load_translations()

HELP_TEXT = (
    "/help - show this help<br>"
    "/date - show current date<br>"
    "/time - show current time<br>"
    "/uptime - show app uptime<br>"
    "/server-uptime - show server uptime<br>"
    "/rules - show rules<br>"
    "/clear - clear current public room (admin)<br>"
    "/ban username reason - ban user (admin)<br>"
    "/unban username - unban user (admin)<br>"
    "/tempban @username 30m reason - temp ban user (admin)<br>"
    "/pin message_id - pin message in current public room (admin)<br>"
    "/unpin message_id - unpin message in current public room (admin)"
)
ANNOUNCEMENT_ROOM = "room:general"


try:
    with open("config.json", "r", encoding="utf-8") as config_file:
        config = json.load(config_file)
except FileNotFoundError:
    config = {}

def split_app_name_tokens(app_name):
    chunks = []
    for segment in re.split(r"[\s_-]+", app_name.strip()):
        chunks.extend(
            part
            for part in re.findall(r"[A-Z]?[a-z0-9]+|[A-Z]+(?=[A-Z]|$)", segment)
            if part
        )
    return chunks


def get_app_initials(app_name):
    tokens = split_app_name_tokens(app_name)
    if len(tokens) >= 2:
        return "".join(token[0].upper() for token in tokens[:2])
    if tokens:
        return tokens[0][:2].upper()
    return "IC"


def get_brand_wordmark_width(app_name):
    clean_length = max(len(app_name.strip()), 4)
    return max(360, min(980, 150 + clean_length * 28))


APP_NAME = str(
    os.getenv(
        "APP_NAME",
        config.get("app_name", TRANSLATIONS["en"].get("app.name", "IntraChat")),
    )
).strip() or "IntraChat"
APP_INITIALS = get_app_initials(APP_NAME)

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", config.get("discord_webhook_url", ""))
SERVER_ID = os.getenv("SERVER_ID", config.get("server_id", "default_server"))
TENOR_API_KEY = os.getenv("TENOR_API_KEY", config.get("tenor_api_key", ""))
CHAT_ROOMS = config.get("chat_rooms", DEFAULT_CHAT_ROOMS)
ROOMS_BY_KEY = {room["key"]: room for room in CHAT_ROOMS}

start_time = datetime.now()
online_users = set()
user_sid_map = defaultdict(set)
user_connection_counts = defaultdict(int)

app = Flask(__name__)

secret_key = os.getenv("FLASK_SECRET_KEY") or config.get("FLASK_SECRET_KEY", "")
if not secret_key:
    raise RuntimeError(
        "FLASK_SECRET_KEY environment variable is required. "
        "Generate a strong key and configure it before starting the app."
    )

app.secret_key = secret_key
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///chat.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

socketio = SocketIO(app, cors_allowed_origins="*")
db.init_app(app)

login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message = None


def log_to_discord(message):
    if not DISCORD_WEBHOOK_URL:
        return
    try:
        requests.post(DISCORD_WEBHOOK_URL, json={"content": message}, timeout=5)
    except Exception as exc:
        print("Failed to send to Discord:", exc)


def get_system_uptime():
    return time.time() - psutil.boot_time()


def format_uptime(seconds):
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    return f"{hours}h {minutes}m {secs}s"


def ensure_database_schema():
    db.create_all()
    inspector = inspect(db.engine)

    user_columns = {column["name"] for column in inspector.get_columns("user")}
    if "language" not in user_columns:
        db.session.execute(
            text("ALTER TABLE user ADD COLUMN language VARCHAR(8) DEFAULT 'sk'")
        )

    chat_columns = {column["name"] for column in inspector.get_columns("chat_message")}
    if "room_key" not in chat_columns:
        db.session.execute(
            text(
                "ALTER TABLE chat_message "
                "ADD COLUMN room_key VARCHAR(120) DEFAULT 'room:general'"
            )
        )

    db.session.execute(text("UPDATE user SET language = COALESCE(language, 'sk')"))
    db.session.execute(
        text("UPDATE chat_message SET room_key = COALESCE(room_key, 'room:general')")
    )
    db.session.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_chat_message_room_key "
            "ON chat_message (room_key)"
        )
    )
    db.session.commit()


with app.app_context():
    ensure_database_schema()


@login_manager.user_loader
def load_user(user_id):
    if not user_id or not str(user_id).isdigit():
        return None
    return db.session.get(User, int(user_id))


@login_manager.unauthorized_handler
def unauthorized():
    return redirect(url_for("login", next=request.path))


def admin_required(func):
    @wraps(func)
    @login_required
    def wrapper(*args, **kwargs):
        if not current_user.is_admin_user:
            abort(403)
        return func(*args, **kwargs)

    return wrapper


def get_language():
    if current_user.is_authenticated and current_user.language in AVAILABLE_LANGUAGES:
        return current_user.language
    session_language = session.get("language", "sk")
    return session_language if session_language in AVAILABLE_LANGUAGES else "sk"


def t(key, lang=None, **kwargs):
    language = lang if lang in AVAILABLE_LANGUAGES else get_language()
    if key == "app.name":
        value = APP_NAME
    else:
        value = TRANSLATIONS.get(language, {}).get(key) or TRANSLATIONS["en"].get(key, key)
    if kwargs:
        return value.format(**kwargs)
    return value


def safe_redirect_target(target, fallback):
    if target and target.startswith("/"):
        return target
    return fallback


def normalize_picture(path_value):
    if not path_value:
        return "/propic/default.png"
    if path_value.startswith("/propic/"):
        return path_value
    return f"/propic/{path_value.lstrip('/')}"


def parse_ban_until(raw_value):
    if not raw_value:
        return None
    if isinstance(raw_value, datetime):
        return raw_value if raw_value.tzinfo else raw_value.replace(tzinfo=timezone.utc)
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(str(raw_value), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(str(raw_value))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def refresh_ban_status(user):
    if not user:
        return
    ban_until = parse_ban_until(user.ban_until)
    if user.is_banned_user and ban_until and datetime.now(timezone.utc) >= ban_until:
        user.is_banned = "No"
        user.ban_reason = None
        user.ban_until = None
        db.session.commit()


def ban_redirect(user):
    params = {"ban_reason": user.ban_reason or "No reason provided."}
    ban_until = parse_ban_until(user.ban_until)
    if ban_until:
        params["ban_until"] = ban_until.isoformat()
    return redirect(url_for("ban", **params))


def build_pm_key(user_a_id, user_b_id):
    left, right = sorted([int(user_a_id), int(user_b_id)])
    return f"pm:{left}:{right}"


def personal_room(user_id):
    return f"user:{int(user_id)}"


def is_public_room(room_key):
    return room_key in ROOMS_BY_KEY


def parse_pm_users(room_key):
    parts = room_key.split(":")
    if len(parts) != 3 or parts[0] != "pm":
        return None
    if not parts[1].isdigit() or not parts[2].isdigit():
        return None
    return int(parts[1]), int(parts[2])


def can_access_conversation(room_key, user):
    if not room_key or not user:
        return False
    if is_public_room(room_key):
        return True
    pm_users = parse_pm_users(room_key)
    return bool(pm_users and int(user.id) in pm_users)


def serialize_user_summary(user):
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_label,
        "rank": user.rank or "",
        "profile_picture": normalize_picture(user.profile_picture),
        "is_admin": user.is_admin_user,
        "language": user.language or "sk",
    }


def serialize_message(message, lookup=None):
    if lookup is None:
        usernames = [message.username]
        lookup = {
            user.username: user
            for user in User.query.filter(User.username.in_(usernames)).all()
        }

    author = lookup.get(message.username)
    return {
        "id": message.id,
        "message": message.message,
        "html": message.message,
        "timestamp": message.timestamp,
        "author": message.username,
        "display_name": author.display_label if author else message.username,
        "rank": author.rank if author and author.rank else "",
        "profile_picture": normalize_picture(author.profile_picture if author else None),
        "room_key": message.room_key or ANNOUNCEMENT_ROOM,
        "is_pinned": bool(message.is_pinned),
    }


def build_system_payload(html, room_key, timestamp=None):
    payload_time = timestamp or datetime.now().strftime("%H:%M:%S")
    return {
        "id": -1,
        "message": html,
        "html": html,
        "timestamp": payload_time,
        "author": "System",
        "display_name": t("chat.system", "en"),
        "rank": "",
        "profile_picture": "/propic/default.png",
        "room_key": room_key,
        "is_pinned": False,
    }


def store_system_message(html, room_key):
    message = ChatMessage(
        username="System",
        message=html,
        formatted_message=html,
        timestamp=datetime.now().strftime("%H:%M:%S"),
        room_key=room_key,
    )
    db.session.add(message)
    db.session.commit()
    return message


def emit_to_conversation(event_name, payload, room_key):
    if is_public_room(room_key):
        socketio.emit(event_name, payload, room=room_key)
        return

    pm_users = parse_pm_users(room_key)
    if not pm_users:
        return
    delivered_to = set()
    for user_id in pm_users:
        room_name = personal_room(user_id)
        if room_name not in delivered_to:
            socketio.emit(event_name, payload, room=room_name)
            delivered_to.add(room_name)


def emit_to_user(user, event_name, payload):
    socketio.emit(event_name, payload, room=personal_room(user.id))


def load_history(room_key):
    messages = (
        ChatMessage.query.filter_by(room_key=room_key)
        .order_by(ChatMessage.id.asc())
        .all()
    )
    usernames = {message.username for message in messages if message.username}
    lookup = (
        {user.username: user for user in User.query.filter(User.username.in_(usernames)).all()}
        if usernames
        else {}
    )
    return [serialize_message(message, lookup) for message in messages]


def get_pinned_message(room_key):
    return (
        ChatMessage.query.filter_by(room_key=room_key, is_pinned=True)
        .order_by(ChatMessage.id.desc())
        .first()
    )


def build_announcement_html(raw_text):
    announcement_text = str(escape(raw_text.strip())).replace("\n", "<br>")
    if not announcement_text:
        return ""
    return f"<div class='announcement'>{announcement_text}</div>"


def prepare_user_message(raw_message, is_admin_message=False):
    cleaned = raw_message.strip()
    if not cleaned:
        return ""

    if is_admin_message and cleaned.startswith("ANNOUNCEMENT:"):
        return build_announcement_html(cleaned.split(":", 1)[1])

    if cleaned.startswith(("<img ", "<video", "<audio", "<a ")):
        return cleaned

    gif_pattern = re.compile(r"(https?://[^\s]+\.gif)", re.IGNORECASE)
    if gif_pattern.search(cleaned):
        return gif_pattern.sub(
            r"<img src='\1' style='max-width: 240px; max-height: 240px;' alt='GIF'>",
            cleaned,
        )

    return str(escape(cleaned)).replace("\n", "<br>")


def announcement_target(room_key):
    return room_key if is_public_room(room_key) else ANNOUNCEMENT_ROOM


def is_delete_allowed(message, user):
    return bool(message and user and (message.username == user.username or user.is_admin_user))


def create_ip_log(user):
    ip_address = request.headers.get("X-Forwarded-For", request.remote_addr)
    db.session.add(
        IPLog(
            username=user.username,
            ip_address=ip_address,
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )
    )
    db.session.commit()


def temp_ban_user(target_username, duration_str, reason, admin_username):
    match = re.fullmatch(r"(\d+)([smhd])", duration_str.strip().lower())
    if not match:
        return False, "Invalid ban duration. Use 30m, 2h or 1d."

    amount = int(match.group(1))
    unit = match.group(2)
    delta_map = {
        "s": timedelta(seconds=amount),
        "m": timedelta(minutes=amount),
        "h": timedelta(hours=amount),
        "d": timedelta(days=amount),
    }
    user = User.query.filter_by(username=target_username).first()
    if not user:
        return False, f"User {escape(target_username)} does not exist."

    ban_until = datetime.now(timezone.utc) + delta_map[unit]
    user.is_banned = "Yes"
    user.ban_reason = reason
    user.ban_until = ban_until.isoformat()
    db.session.add(
        ban_log(
            type="tempban",
            user=user.username,
            reason=reason,
            admin=admin_username,
            time=datetime.now().strftime("%H:%M:%S"),
        )
    )
    db.session.commit()
    return (
        True,
        f"[<i>{datetime.now().strftime('%H:%M:%S')}</i>] "
        f"⏱️ <b>{escape(user.username)}</b> was temporarily banned until "
        f"{ban_until.strftime('%Y-%m-%d %H:%M:%S UTC')}. Reason: <i>{escape(reason)}</i>",
    )


def command_response(html, room_key, broadcast=False, reload_room=False, update_pinned=False):
    return {
        "html": html,
        "room_key": room_key,
        "broadcast": broadcast,
        "reload_room": reload_room,
        "update_pinned": update_pinned,
    }


def handle_command(message_text, room_key, user):
    timestamp = datetime.now().strftime("%H:%M:%S")
    today = datetime.now().strftime("%Y-%m-%d")
    if message_text.startswith("/version"):
        return command_response(f"🤖 Version: {VERSION}", room_key)
    if message_text.startswith("/date"):
        return command_response(f"📅 {today}", room_key)
    if message_text.startswith("/time"):
        return command_response(f"⏰ {timestamp}", room_key)
    if message_text.startswith("/help"):
        return command_response(HELP_TEXT, room_key)
    if message_text.startswith("/rules"):
        return command_response(
            "RULES: no spam, no illegal content, no harassment, report bugs to admins.",
            room_key,
        )
    if message_text.startswith("/uptime"):
        uptime = datetime.now() - start_time
        hours, remainder = divmod(int(uptime.total_seconds()), 3600)
        minutes, seconds = divmod(remainder, 60)
        return command_response(f"🕒 App uptime: {hours}h {minutes}m {seconds}s", room_key)
    if message_text.startswith("/server-uptime"):
        return command_response(f"🖥️ Server uptime: {format_uptime(get_system_uptime())}", room_key)

    if message_text.startswith("/clear"):
        if not user.is_admin_user:
            return command_response("❌ Admin only.", room_key)
        if not is_public_room(room_key):
            return command_response("❌ /clear works only in public rooms.", room_key)
        ChatMessage.query.filter(
            ChatMessage.room_key == room_key,
            ChatMessage.is_pinned.isnot(True),
        ).delete(synchronize_session=False)
        html = f"[<i>{timestamp}</i>] <b>{escape(user.username)}</b> cleared the room."
        store_system_message(html, room_key)
        log_to_discord(f"{SERVER_ID}. {user.username} cleared {room_key}")
        emit_to_conversation("clear_chat", {"room_key": room_key}, room_key)
        return command_response(html, room_key, reload_room=True)

    if message_text.startswith("/pin"):
        if not user.is_admin_user:
            return command_response("❌ Admin only.", room_key)
        if not is_public_room(room_key):
            return command_response("❌ /pin works only in public rooms.", room_key)
        parts = message_text.split(maxsplit=1)
        if len(parts) < 2 or not parts[1].isdigit():
            return command_response("❌ Usage: /pin message_id", room_key)
        target_message = db.session.get(ChatMessage, int(parts[1]))
        if not target_message or target_message.room_key != room_key:
            return command_response("❌ Message not found in this room.", room_key)
        target_message.is_pinned = True
        db.session.commit()
        emit_to_conversation("pinned_updated", {"room_key": room_key}, room_key)
        return command_response(f"📌 Message #{target_message.id} pinned.", room_key, update_pinned=True)

    if message_text.startswith("/unpin"):
        if not user.is_admin_user:
            return command_response("❌ Admin only.", room_key)
        if not is_public_room(room_key):
            return command_response("❌ /unpin works only in public rooms.", room_key)
        parts = message_text.split(maxsplit=1)
        if len(parts) < 2 or not parts[1].isdigit():
            return command_response("❌ Usage: /unpin message_id", room_key)
        target_message = db.session.get(ChatMessage, int(parts[1]))
        if not target_message or target_message.room_key != room_key or not target_message.is_pinned:
            return command_response("❌ Message is not pinned in this room.", room_key)
        target_message.is_pinned = False
        db.session.commit()
        emit_to_conversation("pinned_updated", {"room_key": room_key}, room_key)
        return command_response(f"📌 Message #{target_message.id} unpinned.", room_key, update_pinned=True)

    if message_text.startswith("/ban"):
        if not user.is_admin_user:
            return command_response("❌ Admin only.", room_key)
        parts = message_text.split(" ", 2)
        if len(parts) < 3:
            return command_response("❌ Usage: /ban username reason", room_key)
        target_user = User.query.filter_by(username=parts[1]).first()
        reason = parts[2].strip()
        if not target_user:
            return command_response("❌ User does not exist.", room_key)
        target_user.is_banned = "Yes"
        target_user.ban_reason = reason
        target_user.ban_until = None
        db.session.add(
            ban_log(
                type="ban",
                user=target_user.username,
                reason=reason,
                admin=user.username,
                time=timestamp,
            )
        )
        db.session.commit()
        html = (
            f"[<i>{timestamp}</i>] 🔨 <b>{escape(target_user.username)}</b> was banned. "
            f"Reason: <i>{escape(reason)}</i>"
        )
        target_room = announcement_target(room_key)
        store_system_message(html, target_room)
        emit_to_user(target_user, "force_ban_redirect", {"reason": reason})
        log_to_discord(f"{SERVER_ID}. {user.username} banned {target_user.username}")
        return command_response(html, target_room, broadcast=True)

    if message_text.startswith("/unban"):
        if not user.is_admin_user:
            return command_response("❌ Admin only.", room_key)
        parts = message_text.split(maxsplit=1)
        if len(parts) < 2:
            return command_response("❌ Usage: /unban username", room_key)
        target_user = User.query.filter_by(username=parts[1].strip()).first()
        if not target_user:
            return command_response("❌ User does not exist.", room_key)
        target_user.is_banned = "No"
        target_user.ban_reason = None
        target_user.ban_until = None
        db.session.add(
            ban_log(
                type="unban",
                user=target_user.username,
                reason="unban",
                admin=user.username,
                time=timestamp,
            )
        )
        db.session.commit()
        html = f"[<i>{timestamp}</i>] 🔓 <b>{escape(target_user.username)}</b> was unbanned."
        target_room = announcement_target(room_key)
        store_system_message(html, target_room)
        log_to_discord(f"{SERVER_ID}. {user.username} unbanned {target_user.username}")
        return command_response(html, target_room, broadcast=True)

    if message_text.startswith("/tempban"):
        if not user.is_admin_user:
            return command_response("❌ Admin only.", room_key)
        parts = message_text.split()
        if len(parts) < 4:
            return command_response("❌ Usage: /tempban @username 30m reason", room_key)
        target = parts[1].lstrip("@")
        duration = parts[2]
        reason = " ".join(parts[3:])
        success, html = temp_ban_user(target, duration, reason, user.username)
        if not success:
            return command_response(f"❌ {html}", room_key)
        target_room = announcement_target(room_key)
        store_system_message(html, target_room)
        target_user = User.query.filter_by(username=target).first()
        if target_user:
            emit_to_user(
                target_user,
                "force_ban_redirect",
                {"reason": reason, "ban_until": target_user.ban_until},
            )
        log_to_discord(f"{SERVER_ID}. {user.username} temp banned {target}")
        return command_response(html, target_room, broadcast=True)

    if message_text.startswith("/makeadmin") and user.username == "System":
        if not user.is_admin_user:
            return command_response("❌ Admin only.", room_key)
        parts = message_text.split(maxsplit=1)
        if len(parts) < 2:
            return command_response("❌ Usage: /makeadmin username", room_key)
        target_user = User.query.filter_by(username=parts[1].strip()).first()
        if not target_user:
            return command_response("❌ User does not exist.", room_key)
        target_user.is_admin = "1"
        db.session.commit()
        socketio.emit("refresh_page")
        target_room = announcement_target(room_key)
        html = f"🛡️ {escape(target_user.username)} is now an admin."
        store_system_message(html, target_room)
        return command_response(html, target_room, broadcast=True)

    if message_text.startswith("/deladmin") and user.username == "System":
        if not user.is_admin_user:
            return command_response("❌ Admin only.", room_key)
        parts = message_text.split(maxsplit=1)
        if len(parts) < 2:
            return command_response("❌ Usage: /deladmin username", room_key)
        target_user = User.query.filter_by(username=parts[1].strip()).first()
        if not target_user:
            return command_response("❌ User does not exist.", room_key)
        target_user.is_admin = "0"
        db.session.commit()
        socketio.emit("refresh_page")
        target_room = announcement_target(room_key)
        html = f"🛡️ {escape(target_user.username)} is no longer an admin."
        store_system_message(html, target_room)
        return command_response(html, target_room, broadcast=True)

    return command_response(f"❌ Unknown command: {escape(message_text)}", room_key)


@app.before_request
def enforce_ban_on_http_routes():
    if not current_user.is_authenticated:
        return None
    if request.endpoint in {
        "static",
        "login",
        "ban",
        "profile_pic",
        "set_language",
        "brand_logo",
    }:
        return None
    refresh_ban_status(current_user)
    if current_user.is_banned_user:
        session["language"] = current_user.language or session.get("language", "sk")
        return ban_redirect(current_user)
    return None


@app.context_processor
def inject_template_context():
    return {
        "api_url": "https://tenor.googleapis.com/v2/search",
        "api_key": TENOR_API_KEY,
        "app_name": APP_NAME,
        "app_initials": APP_INITIALS,
        "version": VERSION,
        "available_languages": AVAILABLE_LANGUAGES,
        "ui_language": get_language(),
        "translations": TRANSLATIONS,
        "t": t,
    }


@app.get("/brand/<variant>.svg")
def brand_logo(variant):
    if variant == "wordmark":
        svg = render_template(
            "branding/wordmark.svg",
            app_name=APP_NAME,
            app_initials=APP_INITIALS,
            brand_width=get_brand_wordmark_width(APP_NAME),
        )
    elif variant == "mark":
        svg = render_template(
            "branding/mark.svg",
            app_name=APP_NAME,
            app_initials=APP_INITIALS,
        )
    else:
        abort(404)

    response = Response(svg, mimetype="image/svg+xml")
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/", methods=["GET", "POST"])
@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        refresh_ban_status(current_user)
        if current_user.is_banned_user:
            return ban_redirect(current_user)
        return redirect(url_for("chat"))

    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        remember = request.form.get("remember") == "on"
        user = User.query.filter_by(username=username).first()

        if not user or not check_password_hash(user.password, password):
            error = t("login.invalid")
        else:
            refresh_ban_status(user)
            if user.is_banned_user:
                session["language"] = user.language or session.get("language", "en")
                return ban_redirect(user)
            login_user(user, remember=remember)
            session["language"] = user.language or session.get("language", "en")
            create_ip_log(user)
            next_target = safe_redirect_target(
                request.form.get("next") or request.args.get("next"),
                url_for("chat"),
            )
            return redirect(next_target)

    return render_template("login.html", error=error, next_url=request.args.get("next", ""))


@app.route("/logout")
def logout():
    language = get_language()
    if current_user.is_authenticated:
        logout_user()
    session["language"] = language
    return redirect(url_for("login"))


@app.post("/set_language")
def set_language():
    selected = request.form.get("language", "sk")
    if selected not in AVAILABLE_LANGUAGES:
        selected = "sk"
    session["language"] = selected
    if current_user.is_authenticated:
        current_user.language = selected
        db.session.commit()
    next_target = safe_redirect_target(
        request.form.get("next") or request.referrer,
        url_for("chat" if current_user.is_authenticated else "login"),
    )
    return redirect(next_target)


@app.route("/ban")
def ban():
    return render_template(
        "banned.html",
        ban_reason=request.args.get("ban_reason", "No reason provided."),
        ban_until=request.args.get("ban_until"),
    )


@app.route("/chat")
@login_required
def chat():
    direct_users = User.query.filter(User.id != current_user.id).order_by(User.username.asc()).all()
    room_payload = [
        {"key": room["key"], "icon": room["icon"], "label": t(room["label_key"])}
        for room in CHAT_ROOMS
    ]
    return render_template(
        "chat.html",
        chat_rooms=room_payload,
        direct_users=[serialize_user_summary(user) for user in direct_users],
        current_user_payload=serialize_user_summary(current_user),
        ui_messages={
            "uploading": t("chat.uploading"),
            "upload_error": t("chat.upload_error"),
            "no_messages": t("chat.no_messages"),
            "search_placeholder": t("chat.search_gif_placeholder"),
            "no_gifs": t("chat.no_gifs"),
            "gif_error": t("chat.gif_error"),
            "chat_pm_with": t("chat.pm_with"),
            "chat_room": t("chat.room"),
            "chat_pinned": t("chat.pinned"),
            "chat_online": t("chat.online"),
        },
    )


@app.route("/propic/<filename>")
def profile_pic(filename):
    return send_from_directory(os.path.join(app.root_path, "propic"), filename)


@app.route("/history")
@login_required
def get_history():
    room_key = request.args.get("conversation", ANNOUNCEMENT_ROOM)
    if not can_access_conversation(room_key, current_user):
        return jsonify({"error": "Forbidden"}), 403
    return jsonify(load_history(room_key))


@app.route("/pinned")
@login_required
def pinned():
    room_key = request.args.get("conversation", ANNOUNCEMENT_ROOM)
    if not can_access_conversation(room_key, current_user):
        return jsonify({"error": "Forbidden"}), 403
    pinned_message = get_pinned_message(room_key)
    if not pinned_message:
        return jsonify({"pinned": None})
    return jsonify({"pinned": serialize_message(pinned_message)})


@app.route("/stats/online_count")
@login_required
def online_count():
    total_users = User.query.filter(User.username != "System").count()
    return jsonify({"online": len(online_users), "total": total_users})


@app.route("/change_password", methods=["GET", "POST"])
@login_required
def change_password():
    if request.method == "POST":
        old_password = request.form.get("old_password", "")
        new_password = request.form.get("new_password", "")
        if not check_password_hash(current_user.password, old_password):
            return t("password.invalid_old"), 403
        current_user.password = generate_password_hash(new_password)
        db.session.commit()
        log_to_discord(f"{SERVER_ID}. {current_user.username} changed password")
        return t("password.updated")
    return render_template("change_password.html")


@app.route("/add_user", methods=["GET", "POST"])
@admin_required
def add_user():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        rank = request.form.get("rank", "").strip()
        display_name = request.form.get("display_name", "").strip()
        language = request.form.get("language", "sk")

        if User.query.filter_by(username=username).first():
            return "User already exists.", 409
        if language not in AVAILABLE_LANGUAGES:
            language = "sk"

        user = User(
            username=username,
            password=generate_password_hash(password),
            rank=rank,
            display_name=display_name or username,
            is_admin="0",
            is_banned="No",
            language=language,
            profile_picture="/propic/default.png",
        )
        db.session.add(user)
        db.session.commit()
        log_to_discord(f"{SERVER_ID}. Admin {current_user.username} added user {username}")
        socketio.emit("refresh_page")
        return redirect(url_for("admin_user_list"))

    return render_template("add_user.html")


@app.route("/user/<int:user_id>", methods=["GET", "POST"])
@login_required
def user_profile(user_id):
    user = db.session.get(User, user_id)
    if not user:
        abort(404)

    if request.method == "POST":
        if current_user.id != user.id:
            abort(403)

        display_name = request.form.get("display_name", "").strip()
        language = request.form.get("language", user.language or "sk")
        if display_name:
            user.display_name = display_name
        if language in AVAILABLE_LANGUAGES:
            user.language = language
            session["language"] = language

        file = request.files.get("profile_pic")
        if file and file.filename:
            propic_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "propic")
            os.makedirs(propic_dir, exist_ok=True)
            filename = secure_filename(f"user_{user.id}.png")
            file.save(os.path.join(propic_dir, filename))
            user.profile_picture = f"/propic/{filename}"
            log_to_discord(f"{SERVER_ID}. {current_user.username} changed profile picture")

        db.session.commit()
        socketio.emit("refresh_page")
        return redirect(url_for("user_profile", user_id=user.id))

    return render_template("user_profile.html", user=user)


@app.route("/admin/users")
@admin_required
def admin_user_list():
    users = User.query.order_by(User.id.asc()).all()
    return render_template("admin_users.html", users=users)


@app.post("/update_rank")
@admin_required
def update_rank():
    user = db.session.get(User, int(request.form.get("user_id", 0)))
    if user:
        user.rank = request.form.get("new_rank", "").strip()
        db.session.commit()
        html = (
            f"[<i>{datetime.now().strftime('%H:%M:%S')}</i>] 🏅 "
            f"Admin <b>{escape(current_user.username)}</b> changed rank of "
            f"<b>{escape(user.username)}</b> to <b>{escape(user.rank or '-')}</b>."
        )
        message = store_system_message(html, ANNOUNCEMENT_ROOM)
        emit_to_conversation("message", serialize_message(message), ANNOUNCEMENT_ROOM)
        log_to_discord(f"{SERVER_ID}. {current_user.username} changed rank of {user.username} to {user.rank}")
        socketio.emit("refresh_page")
    return redirect(url_for("admin_user_list"))


@app.post("/ban_user")
@admin_required
def ban_user():
    user = db.session.get(User, int(request.form.get("user_id", 0)))
    reason = request.form.get("reason", "No reason provided").strip()
    if user:
        user.is_banned = "Yes"
        user.ban_reason = reason
        user.ban_until = None
        db.session.add(
            ban_log(
                type="ban",
                user=user.username,
                reason=reason,
                admin=current_user.username,
                time=datetime.now().strftime("%H:%M:%S"),
            )
        )
        db.session.commit()
        html = (
            f"[<i>{datetime.now().strftime('%H:%M:%S')}</i>] ⛔ "
            f"<b>{escape(user.username)}</b> was banned by "
            f"<b>{escape(current_user.username)}</b>. Reason: <i>{escape(reason)}</i>"
        )
        message = store_system_message(html, ANNOUNCEMENT_ROOM)
        emit_to_conversation("message", serialize_message(message), ANNOUNCEMENT_ROOM)
        emit_to_user(user, "force_ban_redirect", {"reason": reason})
        log_to_discord(f"{SERVER_ID}. {current_user.username} banned {user.username}")
    return redirect(url_for("admin_user_list"))


@app.post("/unban_user")
@admin_required
def unban_user():
    user = db.session.get(User, int(request.form.get("user_id", 0)))
    if user:
        user.is_banned = "No"
        user.ban_reason = None
        user.ban_until = None
        db.session.add(
            ban_log(
                type="unban",
                user=user.username,
                reason="unban",
                admin=current_user.username,
                time=datetime.now().strftime("%H:%M:%S"),
            )
        )
        db.session.commit()
        html = f"[<i>{datetime.now().strftime('%H:%M:%S')}</i>] 🔓 <b>{escape(user.username)}</b> was unbanned."
        message = store_system_message(html, ANNOUNCEMENT_ROOM)
        emit_to_conversation("message", serialize_message(message), ANNOUNCEMENT_ROOM)
        log_to_discord(f"{SERVER_ID}. {current_user.username} unbanned {user.username}")
    return redirect(url_for("admin_user_list"))


@app.post("/tempban")
@admin_required
def temp_ban():
    user = db.session.get(User, int(request.form.get("user_id", 0)))
    minutes = int(request.form.get("minutes", 0))
    reason = request.form.get("reason", "No reason provided").strip()
    if user and minutes > 0:
        ban_until = datetime.now(timezone.utc) + timedelta(minutes=minutes)
        user.is_banned = "Yes"
        user.ban_reason = reason
        user.ban_until = ban_until.isoformat()
        db.session.add(
            ban_log(
                type="tempban",
                user=user.username,
                reason=reason,
                admin=current_user.username,
                time=datetime.now().strftime("%H:%M:%S"),
            )
        )
        db.session.commit()
        html = (
            f"[<i>{datetime.now().strftime('%H:%M:%S')}</i>] ⏱️ "
            f"<b>{escape(user.username)}</b> was temporarily banned for {minutes} minutes. "
            f"Reason: <i>{escape(reason)}</i>"
        )
        message = store_system_message(html, ANNOUNCEMENT_ROOM)
        emit_to_conversation("message", serialize_message(message), ANNOUNCEMENT_ROOM)
        emit_to_user(user, "force_ban_redirect", {"reason": reason, "ban_until": user.ban_until})
        log_to_discord(f"{SERVER_ID}. {current_user.username} temp banned {user.username}")
    return redirect(url_for("admin_user_list"))


@app.post("/delete_user")
@admin_required
def delete_user():
    user = db.session.get(User, int(request.form.get("user_id", 0)))
    if not user:
        return redirect(url_for("admin_user_list"))
    if user.id == current_user.id:
        return t("admin.delete_blocked"), 403

    sid_set = set(user_sid_map.get(user.username, set()))
    online_users.discard(user.username)
    user_sid_map.pop(user.username, None)
    user_connection_counts.pop(user.username, None)

    ChatMessage.query.filter(
        or_(
            ChatMessage.username == user.username,
            ChatMessage.room_key.like(f"pm:{user.id}:%"),
            ChatMessage.room_key.like(f"pm:%:{user.id}"),
        )
    ).delete(synchronize_session=False)
    IPLog.query.filter_by(username=user.username).delete(synchronize_session=False)
    ban_log.query.filter(
        or_(ban_log.user == user.username, ban_log.admin == user.username)
    ).delete(synchronize_session=False)
    db.session.delete(user)
    db.session.commit()

    for sid in sid_set:
        socketio.emit("force_logout", {"reason": "account_deleted"}, room=sid)

    log_to_discord(f"{SERVER_ID}. {current_user.username} deleted user {user.username}")
    socketio.emit("refresh_page")
    return redirect(url_for("admin_user_list"))


@app.route("/upload", methods=["POST"])
@login_required
def upload_file():
    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"error": "No file uploaded"}), 400

    upload_folder = os.path.join(app.root_path, "uploads")
    os.makedirs(upload_folder, exist_ok=True)
    filename = f"{int(time.time())}_{secure_filename(file.filename)}"
    file.save(os.path.join(upload_folder, filename))
    return jsonify({"file_url": url_for("uploaded_file", filename=filename)})


@app.route("/uploads/<filename>")
@login_required
def uploaded_file(filename):
    return send_from_directory(os.path.join(app.root_path, "uploads"), filename)


def hourly_broadcast():
    while True:
        time.sleep(1800)
        with app.app_context():
            html = random.choice(
                [
                    "💡 Tip: use /help to list available commands.",
                    "📌 Pinned messages survive /clear in the current room.",
                    "🛡️ Contact an admin if you hit a moderation issue.",
                    f"📅 Today is {datetime.now().strftime('%Y-%m-%d')}.",
                    f"⏰ Current time is {datetime.now().strftime('%H:%M:%S')}.",
                    f"👥 Online now: {len(online_users)}.",
                ]
            )
            message = store_system_message(
                f"[<i>{datetime.now().strftime('%H:%M:%S')}</i>] 🤖 <i>{html}</i>",
                ANNOUNCEMENT_ROOM,
            )
            emit_to_conversation("message", serialize_message(message), ANNOUNCEMENT_ROOM)


@socketio.on("connect")
def handle_connect():
    if not current_user.is_authenticated:
        return False

    refresh_ban_status(current_user)
    if current_user.is_banned_user:
        emit(
            "force_ban_redirect",
            {"reason": current_user.ban_reason or "No reason", "ban_until": current_user.ban_until},
        )
        disconnect()
        return

    username = current_user.username
    user_sid_map[username].add(request.sid)
    user_connection_counts[username] += 1
    join_room(personal_room(current_user.id))
    for room in CHAT_ROOMS:
        join_room(room["key"])

    if user_connection_counts[username] == 1:
        online_users.add(username)


@socketio.on("disconnect")
def handle_disconnect():
    if not current_user.is_authenticated:
        return

    username = current_user.username
    sid_set = user_sid_map.get(username)
    if sid_set and request.sid in sid_set:
        sid_set.discard(request.sid)
        if not sid_set:
            user_sid_map.pop(username, None)

    if username in user_connection_counts:
        user_connection_counts[username] -= 1
        if user_connection_counts[username] <= 0:
            user_connection_counts.pop(username, None)
            online_users.discard(username)


@socketio.on("delete_message")
def handle_delete_message(data):
    if not current_user.is_authenticated:
        return

    message_id = data.get("id")
    message = db.session.get(ChatMessage, int(message_id)) if str(message_id).isdigit() else None
    if not is_delete_allowed(message, current_user):
        return
    room_key = message.room_key or ANNOUNCEMENT_ROOM
    was_pinned = bool(message.is_pinned)
    db.session.delete(message)
    db.session.commit()
    emit_to_conversation("delete_message", {"id": message.id, "room_key": room_key}, room_key)
    if was_pinned:
        emit_to_conversation("pinned_updated", {"room_key": room_key}, room_key)


@socketio.on("message")
def handle_message(payload):
    if not current_user.is_authenticated:
        disconnect()
        return

    refresh_ban_status(current_user)
    if current_user.is_banned_user:
        emit(
            "force_ban_redirect",
            {"reason": current_user.ban_reason or "No reason", "ban_until": current_user.ban_until},
        )
        disconnect()
        return

    if isinstance(payload, str):
        payload = {"message": payload, "conversation": ANNOUNCEMENT_ROOM}

    room_key = payload.get("conversation", ANNOUNCEMENT_ROOM)
    message_text = payload.get("message", "").strip()
    if not can_access_conversation(room_key, current_user) or not message_text:
        return

    if message_text.startswith("/"):
        result = handle_command(message_text, room_key, current_user)
        if result["broadcast"]:
            message = (
                ChatMessage.query.filter_by(room_key=result["room_key"])
                .order_by(ChatMessage.id.desc())
                .first()
            )
            if message:
                emit_to_conversation("message", serialize_message(message), result["room_key"])
        else:
            emit("message", build_system_payload(result["html"], room_key))
        return

    html = prepare_user_message(message_text, current_user.is_admin_user)
    if not html:
        return

    message = ChatMessage(
        username=current_user.username,
        message=html,
        formatted_message=html,
        timestamp=datetime.now().strftime("%H:%M:%S"),
        room_key=room_key,
    )
    db.session.add(message)
    db.session.commit()
    log_to_discord(f"{SERVER_ID}. {current_user.username} sent message in {room_key}: {message_text}")
    emit_to_conversation("message", serialize_message(message), room_key)


if __name__ == "__main__":
    threading.Thread(target=hourly_broadcast, daemon=True).start()
    debug_mode = (
        os.getenv("FLASK_DEBUG", "False").lower() == "true"
        or config.get("FLASK_DEBUG", "False") == "True"
    )
    socketio.run(app, host="0.0.0.0", port=5000, debug=debug_mode)
