One-time migration for legacy data:
1. Stop the bot process.
2. Backup DB:
   `cp data/bot.db data/bot.db.bak.$(date +%Y%m%d%H%M%S)`
3. Run migration:
   `python scripts/migrate_url_dedup.py data/bot.db`
4. Start bot again.

Migration script behavior:
- Collapses duplicates by (`url`, `branch`, `commit_hash`, `tag`, `status`, `error_message`).
- Keeps one row per group using alias from the newest `fetched_at` row.
- Retains newest `hc_timestamp` and newest `fetched_at` for each group.
- Recreates `alias` and `url` latest-result indexes.