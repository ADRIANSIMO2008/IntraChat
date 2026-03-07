# New version coming soon

# 🗨️ IntraChat

**IntraChat** is a modern, secure, and extensible real-time web chat platform built with Flask and Socket.IO. Designed for internal team, school, or community communication, it includes full user management, moderation tools, and a rich set of features for smooth interaction.

---

## ✨ Features

- 💬 Real-time chat with support for GIFs, images, and file uploads  
- 👥 User accounts with profile pictures, display names, and ranks  
- 🔒 Secure login with password hashing (bcrypt)  
- ⚙️ Admin panel with user management (ban/unban, rank change, make admin)  
- ⏱️ Temporary bans, full moderation logs, Discord webhook integration  
- 📌 Message pinning and automated system announcements  
- 🌙 Dark mode, emoji picker
- 📆 Full message history with join/leave notifications  
- 🛡️ Commands like `/ban`, `/time`, `/help`, `/uptime`, `/pin`, `/clear`, etc.

---

## 🧪 Technologies Used

- Python 3.10+  
- Flask + Flask-SocketIO  
- SQLAlchemy + SQLite (MySQL optional)  
- HTML / CSS / JS (vanilla, no frontend frameworks)  
- Discord Webhook API (for logging actions)  
- `psutil` for server uptime tracking

---

## 🚀 Getting Started

1. Clone the repo:
    ```bash
    git clone https://github.com/adriansimo2008/intrachat.git
    cd intrachat
    ```

2. Install dependencies:
    ```bash
    pip install -r requirements.txt
    ```

3. Edit config in config.json
    
4. Run the server:
    ```bash
    python intrachat.py
    ```

5. Open [http://localhost:5000](http://localhost:5000) in your browser.

---

## 📁 Project Structure

 - intrachat.py # Main Flask app
 - /templates/ # Jinja2 HTML templates
 - /static/ # JS, CSS, assets
 - /uploads/ # File uploads
 - /propic/ # **Pro**file **pic**tures
 - /instance/ # Database is here

---

## 🔐 Admin & Moderation

- Full admin dashboard  (at /admin/users)
- Create users (at /add_user)
- Permanent and temporary bans with duration parsing (e.g., `30m`, `2h`)  
- Logged to a ban history database table  
- Admin actions auto-posted to Discord via webhook  
- Users can only delete their own messages (or admins can)

---

## 📌 Notes

- Best suited for internal networks (school, workplace, LAN)  
- All messages are monitored and system notifications are automated
- Default user is System and password is system

---

## 📜 License

This project is licensed under a custom license.  
You may fork and modify it for personal or internal use only.

Redistribution or commercial use is strictly prohibited.  
See [LICENSE](LICENSE) for full terms.

---

**Made with ❤️ by Adrian Simo**
