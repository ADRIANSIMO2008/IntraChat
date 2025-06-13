from flask import Flask, render_template, request, redirect, url_for, session, jsonify, send_from_directory, abort
from flask_socketio import SocketIO, send, emit
from datetime import datetime, timedelta, timezone
from database import db, User, ChatMessage, ban_log, IPLog
from sqlalchemy import text
import os
import math
import re
import random
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.routing import BaseConverter
import psutil
import time
import threading
import requests
import json

# Load config
with open("config.json", "r") as config_file:
    config = json.load(config_file)

DISCORD_WEBHOOK_URL = config.get("discord_webhook_url")
server_id = config.get("server_id")
TENOR_API_KEY = config.get("tenor_api_key")
DISCORD_WEBHOOK_URL = config.get("discord_webhook_url")

server_id = server_id or "default_server"

def log_to_discord(message):
    try:
        requests.post(DISCORD_WEBHOOK_URL, json={"content": message})
    except Exception as e:
        print("Failed to send to Discord:", e)

def log_embed_to_discord(title, description, color=0x3498db):
    embed = {
        "title": title,
        "description": description,
        "color": color
    }
    payload = {
        "embeds": [embed]
    }
    try:
        requests.post(DISCORD_WEBHOOK_URL, json=payload)
    except Exception as e:
        print("Failed to send embed to Discord:", e)

def get_system_uptime():
    boot_time = psutil.boot_time()
    current_time = time.time()
    uptime_seconds = current_time - boot_time
    return uptime_seconds

def format_uptime(seconds):
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    seconds = int(seconds % 60)
    return f"{hours}h {minutes}m {seconds}s"

start_time = datetime.now()

app = Flask(__name__)
app.secret_key = 'secret_key'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///chat.db'
#app.config['SQLALCHEMY_DATABASE_URI'] = 'mysql+mysqlconnector://intrachat_server:IntraChat123!@localhost/intrachat'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

socketio = SocketIO(app, cors_allowed_origins="*")
db.init_app(app)

with app.app_context():
    db.create_all()

upload_folder = "uploads"
# Check if upload folder exists
if not os.path.exists(upload_folder):
    os.makedirs(upload_folder)

@app.route('/', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password, request.form['password']):
            if user.is_banned == 'Yes':
                if user.ban_until:
                    ban_until_naive = datetime.strptime(user.ban_until, "%Y-%m-%d %H:%M:%S.%f")
                    if datetime.utcnow() > ban_until_naive:
                        # Ban expired → unban
                        user.is_banned = 'No'
                        user.ban_reason = None
                        user.ban_until = None
                        db.session.commit()
                        print(f"[INFO] User {username} was automatically unbanned (ban expired).")
                        log_to_discord(f"{server_id}. User {username} was automatically unbanned (ban expired).")
                    else:
                        print(f"[INFO] User {username} is still banned until {user.ban_until}.")
                        log_to_discord(f"{server_id}. User {username} is still banned until {user.ban_until}.")

            if user.is_banned == "Yes":
                ban_reason = user.ban_reason or "No reason provided."
                if user.ban_until:
                    ban_until_dt = datetime.fromisoformat(user.ban_until)
                    ban_until = ban_until_dt.strftime("%Y-%m-%dT%H:%M:%S")
                print(f"[INFO] User {username} is banned. Reason: {ban_reason}")
                if user.ban_until:
                    print(f"BAN UNTIL for {username}: {user.ban_until}")
                    return redirect(url_for('ban', ban_reason=ban_reason, ban_until=ban_until))
                else:
                    return redirect(url_for('ban', ban_reason=ban_reason))

            session['username'] = username
            #session['is_admin'] = user.is_admin  
            session['is_admin'] = (str(user.is_admin) == "1" or user.is_admin == 1)
            session['rank'] = user.rank
            session['display_name'] = user.display_name
            session['id'] = user.id
            session['ban_until'] = user.ban_until
            session['profile_picture'] = user.profile_picture
            # LOG IP ADDRESS
            print(request.headers.get('X-Forwarded-For', request.remote_addr))
            ip = request.headers.get('X-Forwarded-For', request.remote_addr)
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            ip_log = IPLog(username=username, ip_address=ip, timestamp=timestamp)
            db.session.add(ip_log)
            db.session.commit()
#            if session.get('verified'):
            return redirect(url_for('chat'))
#            else:
#                return redirect(url_for('verify'))
        else: 
            return "Invalid login credentials!", 401
    return render_template('login.html')

@app.route('/ban')
def ban():
    ban_reason = request.args.get("ban_reason", "No reason provided!")
    ban_until = request.args.get("ban_until")
    return render_template("banned.html", ban_reason=ban_reason, ban_until=ban_until)

@app.route('/chat')
def chat():
#    if session.get('verified') != True:
#        return redirect(url_for('verify'))
    if 'username' not in session:
        return redirect(url_for('login'))
    user = User.query.filter_by(username=session['username']).first()
    return render_template("chat.html")

@app.route('/propic/<filename>')
def profile_pic(filename):
    return send_from_directory('propic', filename)

def serve_profile_picture(filename):
    propic_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "propic")
    if not os.path.exists(propic_dir):
        os.makedirs(propic_dir)
    
    default_png_path = os.path.join(propic_dir, "default.png")
    if not os.path.exists(default_png_path):
        pass
    
    if not os.path.exists(os.path.join(propic_dir, filename)):
        filename = "default.png"
        
    return send_from_directory("propic", filename)

@app.route('/history')
def get_history():
    messages = ChatMessage.query.order_by(ChatMessage.id).all()
    history = []
    
    for msg in messages:
        user = User.query.filter_by(username=msg.username).first()
        
        message_data = {
            "id": msg.id,
            "message": msg.message,
            "html": msg.message, 
            "timestamp": msg.timestamp,
            "author": msg.username,
            "rank": user.rank if user and user.rank else "",
            "display_name": user.display_name if user and user.display_name else msg.username,
        }
        
        if user and user.profile_picture:
            if user.profile_picture.startswith('/propic/'):
                message_data["profile_picture"] = user.profile_picture
            else:
                message_data["profile_picture"] = "/propic/" + user.profile_picture.lstrip('/')
        else:
            message_data["profile_picture"] = "/propic/default.png"
            
        history.append(message_data)
        
    return jsonify(history)

@app.route("/change_password", methods=["GET", "POST"])
def change_password():
    if 'username' not in session:
        return redirect(url_for('login'))

    user = User.query.filter_by(username=session['username']).first()

    if request.method == "POST":
        old_password = request.form['old_password']
        new_password = request.form['new_password']

        if not check_password_hash(user.password, old_password):
            return "Old password is incorrect!", 403

        user.password = generate_password_hash(new_password)
        log_to_discord(f"{server_id}. {session['username']} changed password")
        db.session.commit()
        return "Password changed successfully ✅"

    return render_template("change_password.html")

@app.route('/add_user', methods=['GET', 'POST'])
def add_user():
    if not session.get("is_admin"):
        return "Access denied!", 403

    if request.method == 'POST':
        username = request.form['username']
        password = generate_password_hash(request.form['password'])
        rank = request.form['rank']
        display_name = request.form['display_name']

        if User.query.filter_by(username=username).first():
            return "User already exists!"

        new_user = User(
            username=username,
            password=password,
            rank=rank,
            display_name=display_name,
            is_admin=False,
            is_banned="No"
        )
        db.session.add(new_user)
        log_to_discord(f"{server_id}. Admin {session['username']} added new user: {username}")
        db.session.commit()
        return "User added successfully ✅"
    
    return render_template("add_user.html")

@app.route("/user/<int:id>", methods=["GET", "POST"])
def user_profile(id):
    user = User.query.get(id)
    if not user:
        return "User does not exist", 404

    if request.method == "POST":
        print("SESSION ID:", session.get("id"), type(session.get("id")))
        print("USER PROFILE ID:", id, type(id))

        if int(session.get("id")) != int(id):
            return "No permission", 403

        # Change display name
        new_display_name = request.form.get("display_name")
        if new_display_name:
            user.display_name = new_display_name.strip()

        # Process profile picture
        if "profile_pic" in request.files:
            file = request.files["profile_pic"]
            if file and file.filename != "":
                # Ensure propic directory exists
                propic_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "propic")
                if not os.path.exists(propic_dir):
                    os.makedirs(propic_dir)
                
                # 🔐 secure filename
                from werkzeug.utils import secure_filename
                # 🏷️ filename based on user ID
                filename = f"user_{user.id}.png"
                filepath = os.path.join(propic_dir, secure_filename(filename))
                # 💾 save file
                file.save(filepath)
                log_to_discord(f"{server_id}. {session['username']} changed profile picture")
                
                # 🧠 Save path to DB - always use /propic/ prefix
                user.profile_picture = f"/propic/{filename}"
                
        db.session.commit()
        return redirect(url_for("user_profile", id=id))

    return render_template("user_profile.html", user=user)

def is_user_banned(user):
    now = datetime.now(timezone.utc)
    if user.ban_until and now >= user.ban_until:
        # Ban expired, automatically unban
        user.is_banned = "No"
        user.ban_reason = None
        user.ban_until = None
        db.session.commit()
        return False
    if user.ban_until:
        return now < user.ban_until
    return user.is_banned == "Yes"

@app.route("/admin/users", methods=["GET", "POST"])
def admin_user_list():
    if not session.get("is_admin"):
        return "Access denied", 403

    users = User.query.order_by(User.id).all()
    return render_template("admin_users.html", users=users)

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return "No file uploaded", 400
    file = request.files['file']
    if file.filename == '':
        return "Invalid file", 400
    file_path = os.path.join(upload_folder, file.filename)
    file.save(file_path)
    return jsonify({"message": "File uploaded successfully", "file_url": url_for('uploaded_file', filename=file.filename)})

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(upload_folder, filename)

online_users = set()

# PINNED MESSAGE (only one for simplicity)
pinned_message = None

# TEMP BANS (username: unban_time)
temp_bans = {}

def parse_duration(duration_str):
    match = re.match(r"(\d+)([smhd])", duration_str)
    if not match:
        return None
    amount, unit = match.groups()
    amount = int(amount)
    if unit == "s":
        return timedelta(seconds=amount)
    elif unit == "m":
        return timedelta(minutes=amount)
    elif unit == "h":
        return timedelta(hours=amount)
    elif unit == "d":
        return timedelta(days=amount)
    return None

def temp_ban_user(target_username, duration_str, reason, admin_username):
    delta = parse_duration(duration_str)
    if not delta:
        return "❌ Invalid ban duration. Use e.g. 30m, 2h, 1d."

    ban_until = datetime.now(timezone.utc) + delta

    user = User.query.filter_by(username=target_username).first()
    if not user:
        return f"❌ User {target_username} does not exist."

    user.ban_until = ban_until
    user.is_banned = "Yes"
    user.ban_reason = reason
    db.session.commit()

    log_entry = ban_log(
        type="tempban",
        user=target_username,
        reason=reason,
        admin=admin_username,
        time=datetime.now().strftime("%H:%M:%S")
    )
    db.session.add(log_entry)
    db.session.commit()

    return f"[<i>{datetime.now().strftime('%H:%M:%S')}</i>] ⏱️ User <b>{target_username}</b> has been temporarily banned until {ban_until.strftime('%Y-%m-%d %H:%M:%S UTC')}. Reason: {reason}"

@app.route('/pinned')
def get_pinned():
    return jsonify({'pinned': pinned_message})

@app.route('/stats/online_count')
def online_count():
    total_users = User.query.count()
    online_count = len(online_users)
    return jsonify({
        "online": online_count,
        "total": total_users
    })

# Global variable for spam control during connection
last_join_times = {}
user_sid_map = {}

@app.post("/ban_user")
def ban_user():
    if not session.get("is_admin"):
        return "Forbidden", 403
    user_id = request.form["user_id"]
    reason = request.form["reason"]
    user = User.query.get(user_id)
    if user:
        user.is_banned = "Yes"
        user.ban_reason = reason
        db.session.commit()

        timestamp = datetime.now().strftime("%H:%M:%S")
        log_entry = ban_log(
            type="ban",
            user=user.username,
            reason=reason,
            admin=session['username'],
            time=timestamp
        )
        db.session.add(log_entry)
        log_to_discord(f"{server_id}. {session['username']} banned user {user.username}")
        db.session.commit()

        # ➕ Send message to chat
        msg = f"[<i>{timestamp}</i>] ⛔ <b>{user.username}</b> was banned by admin <b>{session['username']}</b>.<br>Reason: <i>{reason}</i>"
        socketio.emit("message", msg)

    return redirect(url_for("admin_user_list"))

@app.post("/update_rank")
def update_rank():
    if not session.get("is_admin"):
        return "Forbidden", 403

    user_id = request.form["user_id"]
    new_rank = request.form["new_rank"]
    user = User.query.get(user_id)

    if user:
        user.rank = new_rank
        log_to_discord(f"{server_id}. {session['username']} changed rank of user {user.username} to {new_rank}")
        db.session.commit()

        timestamp = datetime.now().strftime("%H:%M:%S")
        message = f"[<i>{timestamp}</i>] 🏅 Admin <b>{session['username']}</b> changed rank of user <b>{user.username}</b> to <b>{new_rank}</b>"
        socketio.emit("message", message, namespace="/")

    return redirect(url_for("admin_user_list"))

def hourly_broadcast():
    while True:
        time.sleep(1800)  # 0.5 hour
        with app.app_context():
            total_users = User.query.count()
            online_users_hourly = len(online_users)
            random_messages = [
                "💡 Tip: Don't forget to change your password occasionally!",
                "🤖 Did you know you can use the /help command to display help?",
                "📌 Pinned messages won't be deleted by /clear!",
                "⏱️ Have you tried the /uptime command?",
                "👋 Don't forget to greet others in the chat!",
                "🛡️ If you have any problems, don't hesitate to contact an administrator.",
                "🔒 Security first! Never share your password.",
                f"📅 Today is {datetime.now().strftime('%Y-%m-%d')}.",
                f"⏰ Current time is {datetime.now().strftime('%H:%M:%S')}.",
                "🖼️ Add your profile picture in MY PROFILE.",
                f"📊 Total number of users: {total_users}.",
                f"👤 Currently online: {online_users_hourly}.",
                "All messages are monitored by the administrator.",
                "Don't forget to read the chat rules.",
                "If you have any questions, don't hesitate to ask an administrator.",
                "RULES: NO SWEARING, NO LAW BREAKING, NO SPAMMING, REPORT ANY CHAT BUGS TO THE ADMIN.",
                "If you don't like something, message the admin."
            ]
            message = random.choice(random_messages)
            timestamp = datetime.now().strftime("%H:%M:%S")
            html = f"[<i>{timestamp}</i>] 🤖 <i>{message}</i>"
            socketio.emit("message", {
                "html": html,
                "id": -1,
                "author": "System"
            })

@app.post("/unban_user")
def unban_user():
    if not session.get("is_admin"):
        return "Forbidden", 403
    user_id = request.form["user_id"]
    user = User.query.get(user_id)
    if user:
        user.is_banned = "No"
        user.ban_reason = None
        user.ban_until = None
        timestamp = datetime.now().strftime("%H:%M:%S")
        log_entry = ban_log(type="unban", user=user.username, reason='unban', admin=session['username'], time=timestamp)
        db.session.add(log_entry)
        log_to_discord(f"{server_id}. {session['username']} unbanned user {user.username}")
        db.session.commit()
    return redirect(url_for("admin_user_list"))

@socketio.on('connect')
def handle_connect():
    global online_users
    username = session.get('username')
    sid = request.sid  # get Socket.IO session ID
    
    if username:
        user_sid_map[username] = sid  # remember for later kick
        now = datetime.now()
        online_users.add(username)
        last_time = last_join_times.get(username)
        user = User.query.filter_by(username=username).first()
        if user and user.is_banned == "Yes":
            reason = user.ban_reason or "No reason"
            target_sid = user_sid_map.get(username)
            if target_sid:
                socketio.emit('force_ban_redirect', {'reason': reason, 'ban_until': user.ban_until}, room=target_sid)
                return  # Don't continue processing the message
        if not last_time or (now - last_time).total_seconds() > 5:
            timestamp = now.strftime("%H:%M:%S")
            join_message = f"<i>[{timestamp}]</i> 🟢 <b>{username}</b> joined the chat"
            socketio.emit("message", {
                "html": join_message,
                "id": -1,
                "author": username
            })
            last_join_times[username] = now

@socketio.on('disconnect')
def handle_disconnect():
    global online_users
    username = session.get('username')
    if username:
        now = datetime.now()
        last_time = last_join_times.get(username)
        online_users.discard(username)
        if not last_time or (now - last_time).total_seconds() > 5:
            # Last connection was long ago – show message
            timestamp = now.strftime("%H:%M:%S")
            leave_message = f"<i>[{timestamp}]</i> 🔴 <b>{username}</b> left the chat"
            socketio.emit("message", {
                "html": leave_message,
                "id": -1,
                "author": username
            })
            last_join_times[username] = now  # update time
            user_sid_map.pop(username, None)

@socketio.on('ban_user')
def handle_user_ban(data):
    banned_username = data.get('username')
    reason = data.get('reason', 'No reason specified')

    user = User.query.filter_by(username=banned_username).first()
    if user:
        user.is_banned = "Yes"
        user.ban_reason = reason
        db.session.commit()

        timestamp = datetime.now().strftime("%H:%M:%S")
        ban_announcement = f"<i>[{timestamp}]</i> ❌ <b>{banned_username}</b> was banned. Reason: {reason}"

        db.session.add(ChatMessage(username="System", message=ban_announcement, timestamp=timestamp))
        log_to_discord(f"{server_id}. {session['username']} banned user {banned_username} (VIA ADMIN PANEL)")
        db.session.commit()

        socketio.emit('message', ban_announcement)

        target_sid = user_sid_map.get(banned_username)
        if target_sid:
            socketio.emit('force_ban_redirect', {'reason': reason}, room=target_sid)

@socketio.on('delete_message')
def handle_delete_message(data):
    msg_id = data.get("id")
    username = session.get("username")
    is_admin = session.get("is_admin")

    message = ChatMessage.query.filter_by(id=msg_id).first()

    if message:
        if message.username == username or is_admin:
            db.session.delete(message)
            db.session.commit()
            socketio.emit('delete_message', {'id': msg_id})  # send to everyone the message ID to remove

@socketio.on('message')
def handle_message(msg):
    username = session.get('username', 'Unknown')
    timestamp = datetime.now().strftime("%H:%M:%S")
    date = datetime.now().strftime("%Y-%m-%d")
    print(f"[LOG] {username}: {msg}")

    # Load user from DB
    user = User.query.filter_by(username=username).first()
    if user and user.is_banned == "Yes":
        reason = user.ban_reason or "No reason"
        target_sid = user_sid_map.get(username)
        if target_sid:
            socketio.emit('force_ban_redirect', {'reason': reason}, room=target_sid)
        return  # Don't continue processing the message

    # Automatic replacement of .gif links with <img>
    if ".gif" in msg and "<img" not in msg:
        gif_pattern = re.compile(r"(https?://[^\s]+\.gif)")
        msg = gif_pattern.sub(r"<img src='\1' style='max-width: 200px; max-height: 200px;' alt='GIF'>", msg)

    # Command processing
    if msg.startswith("/"):
        if msg.startswith("/date") or msg.startswith("/DATE"):
            formatted_msg = f"User {username} used command /date: {date}"
        elif msg.startswith("/time"):
            formatted_msg = f"User {username} used command /time: {timestamp}"
        elif msg.startswith("/help"):
            formatted_msg = (
                "Command: /help - Show help<br>"
                "Command: /date - Show current date<br>"
                "Command: /time - Show current time<br>"
                "Command: /uptime - Show chat server uptime<br>"
                "Command: /server-uptime - Show server uptime<br>"
                "Command: /rules - Show rules<br>"
            )
        elif msg.startswith("/rules"):
            formatted_msg = (
                "RULES: NO SWEARING <br>NO LAW BREAKING <br>DON'T SEND THINGS YOU MIGHT REGRET <br>REPORT ANY CHAT BUGS TO THE ADMIN"
            )
        elif msg.startswith("/clear") and session.get('is_admin', False):
            ChatMessage.query.filter_by(is_pinned=False or None).delete()
            log_to_discord(f"{server_id}. {session['username']} cleared chat")
            db.session.commit()
            formatted_msg = f"[<i>{timestamp}</i>] <b>{username}</b> cleared the chat!"
            socketio.emit("clear_chat")
        elif msg.startswith("/ban") and session.get('is_admin', False):
            parts = msg.split(" ", 2)
            if len(parts) < 3:
                formatted_msg = f"❌ Usage: /ban username reason"
            else:
                target_username = parts[1]
                reason = parts[2]

                user_to_ban = User.query.filter_by(username=target_username).first()
                if user_to_ban:
                    user_to_ban.is_banned = "Yes"
                    user_to_ban.ban_reason = reason
                    log_entry = ban_log(type="ban", user=target_username, reason=reason, admin=username, time=timestamp)
                    db.session.add(log_entry)
                    log_to_discord(f"{server_id}. {username} banned user {target_username}")
                    db.session.commit()
                    formatted_msg = f"[<i>{timestamp}</i>] 🔨 User <b>{target_username}</b> was banned. Reason: {reason}"
                    target_sid = user_sid_map.get(target_username)
                    if target_sid:
                        socketio.emit('force_ban_redirect', {'reason': reason}, room=target_sid)
                    # Save message to DB
                    db.session.add(ChatMessage(username="System", message=formatted_msg, timestamp=timestamp))
                    db.session.commit()
                else:
                    formatted_msg = f"❌ User {target_username} does not exist"
        elif msg.startswith("/unban") and session.get('is_admin', False):
            parts = msg.split(" ", 1)
            if len(parts) < 2:
                formatted_msg = f"❌ Usage: /unban username"
            else:
                target_username = parts[1]
                user_to_unban = User.query.filter_by(username=target_username).first()
                if user_to_unban:
                    user_to_unban.is_banned = ""
                    user_to_unban.ban_reason = ""
                    log_entry = ban_log(type="unban", user=target_username, reason='unban', admin=username, time=timestamp)
                    db.session.add(log_entry)
                    log_to_discord(f"{server_id}. {username} unbanned user {target_username}")
                    db.session.commit()
                    formatted_msg = f"[<i>{timestamp}</i>] 🔨 User <b>{target_username}</b> was unbanned."
                    # Save message to DB
                    db.session.add(ChatMessage(username="System", message=formatted_msg, timestamp=timestamp))
                    db.session.commit()
                else:
                    formatted_msg = f"❌ User {target_username} does not exist"
        elif msg.startswith("/deladmin") and username == "System":
            parts = msg.split(" ", 1)
            if len(parts) < 2:
                formatted_msg = f"Usage: /deladmin username"
            else:
                target_username = parts[1]
                user_to_unadmin = User.query.filter_by(username=target_username).first()
                if user_to_unadmin:
                    user_to_unadmin.is_admin = False
                    db.session.commit()
                    formatted_msg = f"[<i>{timestamp}</i>] 🔨 User <b>{target_username}</b> was removed as ADMIN."
                    # Save message to DB
                    db.session.add(ChatMessage(username="System", message=formatted_msg, timestamp=timestamp))
                    log_to_discord(f"{server_id}. {username} removed admin {target_username}")
                    db.session.commit()
                else:
                    formatted_msg = f"❌ User {target_username} does not exist"
        elif msg.startswith("/makeadmin") and username == "System":
            parts = msg.split(" ", 1)
            if len(parts) < 2:
                formatted_msg = f"Usage: /makeadmin username"
            else:
                target_username = parts[1]
                user_to_admin = User.query.filter_by(username=target_username).first()
                if user_to_admin:
                    user_to_admin.is_admin = True
                    db.session.commit()
                    formatted_msg = f"[<i>{timestamp}</i>] 🔨 User <b>{target_username}</b> was added as ADMIN."
                # Uložiť správu do DB
                    db.session.add(ChatMessage(username="System", message=formatted_msg, timestamp=timestamp))
                    log_to_discord(f"{server_id}. {username} added admin {target_username}")
                    db.session.commit()
                else:
                    formatted_msg = f"❌ User {target_username} does not exist"
        elif msg.startswith("/uptime"):
            uptime = datetime.now() - start_time
            hours, remainder = divmod(uptime.total_seconds(), 3600)
            minutes, seconds = divmod(remainder, 60)
            formatted_msg = f"🕒 Server runs for {int(hours)}h {int(minutes)}m {int(seconds)}s"
        elif msg.startswith("/server-uptime"):
            uptime_seconds = get_system_uptime()
            if uptime_seconds is not None:
                uptime_str = format_uptime(uptime_seconds)
                formatted_msg = f"🖥️ Server uptime: {uptime_str}"
            else:
                formatted_msg = "⚠️ Error."
        elif msg.startswith("/tempban") and session.get('is_admin', False):
            parts = msg.split()
            if len(parts) < 4:
                formatted_msg = "❌ Usage: /tempban @username 2h reason"
            else:
                target = parts[1].lstrip("@")
                duration = parts[2]
                reason = " ".join(parts[3:])
                result = temp_ban_user(target, duration, reason, session["username"])
                formatted_msg = result
        elif msg.startswith("/pin") and session.get('is_admin', False):
            parts = msg.split(" ", 1)
            if len(parts) < 2:
                formatted_msg = "❌ Usage: /pin [message_id]"
            else:
                try:
                    msg_id = int(parts[1])
                    message = ChatMessage.query.get(msg_id)
                    if message:
                        message.is_pinned = True
                        db.session.commit()
                        pinned_msg_html = message.message
                        pinned_message = pinned_msg_html
                        formatted_msg = f"📌 Message ID {msg_id} was pinned."
                    else:
                        formatted_msg = f"❌ Message ID {msg_id} does not exist."
                except ValueError:
                    formatted_msg = "❌ ID must be number."
        elif msg.startswith("/unpin") and session.get('is_admin', False):
            parts = msg.split(" ", 1)
            if len(parts) < 2:
                formatted_msg = "❌ Usage: /unpin [message_id]"
            else:
                try:
                    msg_id = int(parts[1])
                    message = ChatMessage.query.get(msg_id)
                    if message and message.is_pinned:
                        message.is_pinned = False
                        db.session.commit()
                        pinned_message = None
                        formatted_msg = f"📌 Message ID {msg_id} was unpinned."
                    else:
                        formatted_msg = f"❌ Message ID {msg_id} does not exist or is not pinned."
                except ValueError:
                    formatted_msg = "❌ ID must be number."
        # Add more commands here as needed
        else:
            formatted_msg = f"Unrecognized command: {msg}"

        send({
            'html': formatted_msg,
            'id': -1,
            'author': "System",
            'is_admin': session.get('is_admin', False)
        }, broadcast=True)

    else:
        if msg.startswith("ANOUNCEMENT:") and session.get('is_admin', False):
            msg = f"<span style='color: red;font-size: xx-large;'>{msg}</span>"
    
        message_content = msg
    
    # Store the raw message without extra formatting in the database
        new_message = ChatMessage(username=username, message=message_content, timestamp=timestamp)
        db.session.add(new_message)
        log_to_discord(f"{server_id}. {username} sent message: {msg}")
        db.session.commit()
 
        send({
        'id': new_message.id,
        'html': message_content,  # The content to display
        'message': message_content,  # Original message
        'author': username,
        'timestamp': timestamp,
        'is_admin': session.get('is_admin', False),
        'rank': user.rank if user and user.rank else "",
        'display_name': user.display_name if user and user.display_name else username,
        'profile_picture': user.profile_picture if user and user.profile_picture else "/propic/default.png"
    }, broadcast=True)

@app.post("/tempban")
def temp_ban():
    if not session.get("is_admin"):
        return "Forbiden", 403

    user_id = request.form.get("user_id")
    minutes = int(request.form.get("minutes", 0))
    reason = request.form.get("reason", "No reason provided")

    user = User.query.get(user_id)
    if user:
        user.is_banned = "Yes"
        user.ban_reason = reason
        user.ban_until = datetime.now() + timedelta(minutes=minutes) - timedelta(hours=2)  # Subtract 2 hours for UTC+2
        db.session.commit()

        log_entry = ban_log(
            type="tempban",
            user=user.username,
            reason=reason,
            admin=session["username"],
            time=datetime.now().strftime("%H:%M:%S")
        )
        db.session.add(log_entry)
        db.session.commit()

        msg = f"[<i>{datetime.now().strftime('%H:%M:%S')}</i>] ⏱️ User <b>{user.username}</b> was temporarily banned for {minutes} minutes. Reason: {reason}"
        socketio.emit("message", {"html": msg, "id": -1, "author": "System"})
    return redirect(url_for("admin_user_list"))

@app.context_processor
def inject_api_details():
    return {
        'api_url': 'https://tenor.googleapis.com/v2/search',
        'api_key': TENOR_API_KEY, 
    }

if __name__ == '__main__':
    threading.Thread(target=hourly_broadcast, daemon=True).start()
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)
