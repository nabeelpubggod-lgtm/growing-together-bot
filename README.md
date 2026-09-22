# Growing Together V3 — Supabase edition

This version uses Supabase as the persistent database instead of SQLite.

## Features
- 3 community coins required per submission.
- 1 coin for participating in each creator spotlight post.
- 5-coin one-time admin participation bonus.
- Telegram membership verification.
- Automatic eligibility approval by default.
- Duplicate URL protection.
- Daily submission limit.
- Automatic publishing every configured interval.
- Pinned admin participation post.
- User coin balance, earned/spent totals and submission history.
- Admin commands and statistics.

## Supabase requirements
The supplied schema creates these tables and functions:
- `users`
- `submissions`
- `participation`
- `settings`
- `increment_user_coins`
- `spend_user_coins`

The bot must use the Supabase service-role key only on the backend. Never expose it in browser code, public repositories, or client-side JavaScript.

## Environment variables
Set all values from `.env.example`, including:
- `BOT_TOKEN`
- `ADMIN_IDS`
- `CHANNEL_ID`
- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`

The bot no longer uses `DB_PATH` or a local SQLite database.

## Telegram permissions
The bot needs permission to post messages in the channel. To automatically pin the admin post, give the bot the required channel admin permission for editing/pinning messages.

## Automatic approval
`AUTO_APPROVE=true` approves submissions after checking membership, coin balance, daily limit, URL format and duplicate URL. It does not guarantee third-party platform policy approval.

## Admin commands
`/panel`, `/stats`, `/adminpost`, `/publish`, `/queue`, `/pending`, `/approve ID`, `/reject ID reason`, `/lockgroup`, `/unlockgroup`.
