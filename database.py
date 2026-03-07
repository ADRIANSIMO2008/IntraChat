from datetime import datetime

from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy


db = SQLAlchemy()


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, unique=True, primary_key=True)
    username = db.Column(db.String(50), unique=True)
    password = db.Column(db.String(255))
    display_name = db.Column(db.String(50))
    email = db.Column(db.String(100), unique=True)
    is_admin = db.Column(db.Text, default="0")
    rank = db.Column(db.String(50))
    is_banned = db.Column(db.Text, default="No")
    ban_reason = db.Column(db.Text)
    ban_until = db.Column(db.Text)
    profile_picture = db.Column(db.String, default="/propic/default.png")
    language = db.Column(db.String(8), default="sk")

    @property
    def is_admin_user(self):
        return str(self.is_admin).strip().lower() in {"1", "true", "yes"}

    @property
    def is_banned_user(self):
        return str(self.is_banned).strip().lower() in {"1", "true", "yes"}

    @property
    def display_label(self):
        return self.display_name or self.username

    @property
    def avatar_url(self):
        return self.profile_picture or "/propic/default.png"


class ChatMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50))
    message = db.Column(db.Text)
    formatted_message = db.Column(db.Text)
    timestamp = db.Column(db.String(20))
    is_pinned = db.Column(db.Boolean, default=False)
    room_key = db.Column(db.String(120), default="room:general", index=True)


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
    timestamp = db.Column(
        db.String(64),
        default=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )
