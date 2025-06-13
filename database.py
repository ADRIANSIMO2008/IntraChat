from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()

class User(db.Model):
    id = db.Column(db.Integer, unique=True, primary_key=True)
    username = db.Column(db.String(50), unique=True)
    password = db.Column(db.String(50))
    display_name = db.Column(db.String(50))  # Pridanie stĺpca pre zobrazené meno
    email = db.Column(db.String(100), unique=True)  # Pridanie stĺpca pre email
    is_admin = db.Column(db.Text, default=False)  # ← musí byť tu
    rank = db.Column(db.String(50))  # Pridanie stĺpca pre rank
    is_banned = db.Column(db.Integer)
    ban_reason = db.Column(db.Text)
    ban_until = db.Column(db.Text)
    profile_picture = db.Column(db.String, default="/propic/default.png")


class ChatMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50))
    message = db.Column(db.Text)             # raw text
    formatted_message = db.Column(db.Text)   # HTML ver.
    timestamp = db.Column(db.String(20))
    is_pinned = db.Column(db.Boolean, default=False)


class ban_log(db.Model):
    type = db.Column(db.Text)
    id = db.Column(db.Integer, primary_key=True)
    user = db.Column(db.String(64), nullable=False)
    reason = db.Column(db.String(256), nullable=False)
    admin = db.Column(db.String(64), nullable=False)
    time = db.Column(db.Text)

class IPLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64))
    ip_address = db.Column(db.String(64))
    timestamp = db.Column(db.String(64), default=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
