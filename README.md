# CLOVISS KEY

Telegram-based free-key website.

## Features
- CLOVISS KEY landing page
- Your own JOIN CHANNEL link
- Telegram login
- 1 key per Telegram account per 24 hours
- Admin panel for adding keys
- Key history: who received which key
- Telegram notification to admin after every key claim
- SQLite database
- Render/Gunicorn ready

## Important
For production, configure the Telegram Login Widget bot/domain correctly. The website should be served over HTTPS.

### Render
Build Command:
`pip install -r requirements.txt`

Start Command:
`gunicorn app:app`

Environment variables:
- BOT_TOKEN
- BOT_USERNAME (without @)
- ADMIN_ID
- CHANNEL_URL
- SECRET_KEY

### Admin
Open `/admin` and enter your Telegram numeric ID.

### Adding keys
Admin → Add keys → one key per line.
