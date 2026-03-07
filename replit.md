# IntraChat - Real-time Chat Application

## Overview
IntraChat is a modern, secure, and extensible real-time web chat platform built with Flask and Socket.IO. It's designed for internal team, school, or community communication, and includes full user management, moderation tools, and a rich set of features for smooth interaction.

**Current State:** Fully configured and running on Replit. The application is ready to use with the default System/system user account.

## Recent Changes (2025-11-12)
- Configured for Replit environment
- Updated to use environment variables for sensitive configuration (Discord webhook, Tenor API key)
- Set up workflow to run on port 5000
- Configured deployment settings for production (VM deployment)
- Updated .gitignore to exclude generated files and sensitive data

## Key Features
- 💬 Real-time chat with support for GIFs, images, and file uploads
- 👥 User accounts with profile pictures, display names, and ranks
- 🔒 Secure login with password hashing
- ⚙️ Admin panel with user management (ban/unban, rank change, make admin)
- ⏱️ Temporary bans, full moderation logs, Discord webhook integration
- 📌 Message pinning and automated system announcements
- 🌙 Dark mode, emoji picker
- 📆 Full message history with join/leave notifications
- 🛡️ Commands like `/ban`, `/time`, `/help`, `/uptime`, `/pin`, `/clear`, etc.

## Project Architecture

### Technology Stack
- **Backend:** Python 3.11, Flask, Flask-SocketIO, SQLAlchemy
- **Real-time:** Socket.IO with eventlet
- **Database:** SQLite (MySQL optional)
- **Frontend:** Vanilla HTML/CSS/JavaScript
- **Integrations:** Discord Webhook API, Tenor GIF API

### Project Structure
```
/
├── intrachat.py          # Main Flask application
├── database.py           # Database models (User, ChatMessage, ban_log, IPLog)
├── config.json           # Configuration file (optional, uses env vars first)
├── requirements.txt      # Python dependencies
├── templates/            # Jinja2 HTML templates
│   ├── login.html
│   ├── chat.html
│   ├── admin_users.html
│   └── ...
├── static/               # CSS, images, and static assets
│   ├── style.css
│   ├── logo.png
│   └── ...
├── instance/             # Database storage (gitignored)
│   └── chat.db
├── propic/               # Profile pictures
│   └── default.png
└── uploads/              # User file uploads
```

### Database Schema
- **User:** id, username, password, display_name, email, is_admin, rank, is_banned, ban_reason, ban_until, profile_picture
- **ChatMessage:** id, username, message, formatted_message, timestamp, is_pinned
- **ban_log:** id, type, user, reason, admin, time
- **IPLog:** id, username, ip_address, timestamp

### Key Routes
- `/` - Login page
- `/chat` - Main chat interface
- `/add_user` - Admin: Create new user
- `/admin/users` - Admin: User management panel
- `/user/<id>` - User profile page
- `/change_password` - Change password page

## Configuration

### Environment Variables
Set these in the Replit Secrets panel:

**REQUIRED:**
- `FLASK_SECRET_KEY` - **MANDATORY** secret key for Flask sessions. The application will refuse to start without this. Generate one with: `python -c 'import secrets; print(secrets.token_hex(32))'`

**Optional:**
- `DISCORD_WEBHOOK_URL` - Discord webhook for admin notifications
- `SERVER_ID` - Server identifier for Discord logs (default: "default_server")
- `TENOR_API_KEY` - Tenor API key for GIF search functionality
- `FLASK_DEBUG` - Set to "true" to enable debug mode (default: false, NOT recommended for production)

### config.json (Fallback)
If environment variables are not set, the app will fall back to config.json:
```json
{
  "discord_webhook_url": "your_webhook_url",
  "server_id": "IntraServer1",
  "tenor_api_key": "your_tenor_api_key"
}
```

## Default Login
- **Username:** System
- **Password:** system

⚠️ **Important:** Change the default password immediately after first login using the `/change_password` route!

## Admin Commands (in chat)
- `/help` - Show all commands
- `/date` - Show current date
- `/time` - Show current time
- `/uptime` - Show chat server uptime
- `/rules` - Show chat rules
- `/clear` - Clear all non-pinned messages (admin only)
- `/ban <username> <reason>` - Ban a user (admin only)
- `/unban <username>` - Unban a user (admin only)
- `/tempban @<username> <duration> <reason>` - Temporary ban (e.g., 2h, 30m) (admin only)
- `/pin <message>` - Pin a message (admin only)
- `/makeadmin <username>` - Make user admin (System user only)
- `/deladmin <username>` - Remove admin privileges (System user only)

## Development Notes
- The app runs on `0.0.0.0:5000` for proper Replit proxy support
- WebSocket connections are handled by eventlet
- Database is SQLite by default (can be changed to MySQL in intrachat.py line 65-66)
- Profile pictures are stored in `/propic/` directory
- All admin actions are logged to Discord webhook if configured
- Automated system announcements run every 30 minutes

## Deployment
- **Deployment Type:** VM (always running, maintains WebSocket connections)
- **Run Command:** `python intrachat.py`
- The app is configured for production deployment through Replit's deployment system

## Security Considerations
- All passwords are hashed using Werkzeug's security utilities
- Session management via Flask sessions
- IP logging for security auditing
- Admin-only commands and routes
- Ban system with temporary and permanent options
- File upload security with werkzeug.utils.secure_filename

## User Preferences
None specified yet.

## Next Steps / Recommendations
1. Change the default System user password
2. Set up Discord webhook for admin notifications (optional)
3. Set up Tenor API key for GIF search functionality (optional)
4. Create additional user accounts via `/add_user`
5. Customize chat rules in the code or via admin commands
6. Consider migrating to PostgreSQL for production use (Replit offers built-in Postgres)
