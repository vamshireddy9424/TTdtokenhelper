#!/usr/bin/env python3
"""
TTD Slot Monitor with Telegram Bot
- Monitors slot availability every 5 minutes
- Sends Telegram alert when slots open
- When session expires, sends you a Telegram message with login link
- You login on iPhone, copy new authtoken, send it back to the bot
- Script auto-resumes with new token

SETUP:
  pip3 install requests
  
  Then follow SETUP.md instructions to create your Telegram bot.
"""

import requests
import time
import json
import threading
import logging
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger(__name__)

# ============================================================
# CONFIGURATION — Fill these in before running
# ============================================================

# Your Telegram bot token (from @BotFather)
TELEGRAM_BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"

# Your Telegram chat ID (from @userinfobot)
TELEGRAM_CHAT_ID = "YOUR_CHAT_ID_HERE"

# Initial cookies from your last manual login
# Update these once at the start, bot handles renewals after that
STATE = {
    "cookies": {
        "user_id": "18789156",
        "authtoken": "508bd68bad34577f59779d37a777213b9ad0fc2a1668104690",
        "valid_till": "2026-03-12 02:46:28",
        "valid_till_in_utc": "2026-03-11 21:16:28",
    },
    "waiting_for_token": False,
    "last_update_id": 0,
    "running": True,
}

# How often to check slots (seconds)
CHECK_INTERVAL = 300  # 5 minutes

# How early to warn before session expires (seconds)
EXPIRY_WARNING = 600  # Warn 10 mins before expiry

# ============================================================
# TTD API
# ============================================================

API_URL = "https://ttdevasthanams.ap.gov.in/api/sdn/rest/v1/slot/get_availability"
VERIFY_URL = "https://ttdevasthanams.ap.gov.in/api/gatekeeper/verify"
BOOKING_URL = "https://ttdevasthanams.ap.gov.in/slot-booking?flow=sed&flowIdentifier=sed"


def get_headers():
    return {
        "accept": "application/json",
        "accept-language": "en-US,en;q=0.9",
        "authtoken": STATE["cookies"].get("authtoken", ""),
        "referer": BOOKING_URL,
        "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    }


def is_session_valid():
    """Returns (is_valid, minutes_remaining)"""
    try:
        valid_till = datetime.strptime(STATE["cookies"]["valid_till"], "%Y-%m-%d %H:%M:%S")
        remaining_secs = (valid_till - datetime.now()).total_seconds()
        return remaining_secs > 0, int(remaining_secs // 60)
    except Exception:
        return True, 999  # Assume valid if can't parse


def check_slots():
    """Returns list of (date_str, count) for available slots, or None on auth error."""
    try:
        resp = requests.get(
            API_URL,
            headers=get_headers(),
            cookies=STATE["cookies"],
            timeout=15
        )

        if resp.status_code in (401, 403):
            return None  # Auth failed

        if resp.status_code != 200:
            log.warning(f"Unexpected status: {resp.status_code}")
            return []

        data = resp.json()
        if data.get("status") != "success":
            log.warning(f"API non-success: {data}")
            return []

        available = []
        for date_str, info in data.get("result", {}).items():
            if date_str == "blockedDays":
                continue
            avl = info.get("avl", 0)
            if avl > 0:
                try:
                    d = datetime.strptime(date_str, "%Y%m%d")
                    formatted = d.strftime("%d %b %Y (%A)")
                except Exception:
                    formatted = date_str
                available.append((formatted, avl))

        return available

    except requests.exceptions.ConnectionError:
        log.warning("No internet connection.")
        return []
    except Exception as e:
        log.error(f"Error checking slots: {e}")
        return []


# ============================================================
# TELEGRAM
# ============================================================

def tg_send(message):
    """Send a message to your Telegram."""
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code != 200:
            log.warning(f"Telegram send failed: {r.text}")
    except Exception as e:
        log.error(f"Telegram error: {e}")


def tg_get_updates():
    """Poll Telegram for new messages from you."""
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
        params = {
            "offset": STATE["last_update_id"] + 1,
            "timeout": 5,
            "allowed_updates": ["message"]
        }
        r = requests.get(url, params=params, timeout=10)
        if r.status_code == 200:
            return r.json().get("result", [])
    except Exception:
        pass
    return []


def process_telegram_messages():
    """Check for replies from you — specifically new authtoken."""
    updates = tg_get_updates()
    for update in updates:
        STATE["last_update_id"] = update["update_id"]
        msg = update.get("message", {})
        chat_id = str(msg.get("chat", {}).get("id", ""))
        text = msg.get("text", "").strip()

        # Only respond to your own chat
        if chat_id != str(TELEGRAM_CHAT_ID):
            continue

        if STATE["waiting_for_token"]:
            # Expecting new authtoken from user
            if len(text) > 20 and " " not in text:
                # Looks like a token
                STATE["cookies"]["authtoken"] = text
                STATE["waiting_for_token"] = False
                log.info(f"Updated authtoken from Telegram: {text[:10]}...")
                tg_send(
                    "✅ <b>Token updated!</b>\n"
                    "Resuming slot monitoring now...\n\n"
                    "I'll alert you the moment slots open 🙏"
                )
            else:
                tg_send(
                    "⚠️ That doesn't look like a valid token.\n"
                    "Please send <b>only</b> the authtoken value (long string, no spaces)."
                )
        elif text.lower() == "/status":
            valid, mins = is_session_valid()
            slots = check_slots()
            slot_info = f"{len(slots)} date(s) available! 🎉" if slots else "No slots yet."
            tg_send(
                f"📊 <b>Monitor Status</b>\n\n"
                f"Session: {'✅ Valid' if valid else '❌ Expired'} ({mins} mins left)\n"
                f"Slots: {slot_info}\n"
                f"Checking every: {CHECK_INTERVAL // 60} minutes"
            )
        elif text.lower() == "/stop":
            STATE["running"] = False
            tg_send("🛑 Monitor stopped.")


# ============================================================
# SESSION REFRESH FLOW
# ============================================================

def request_new_token():
    """Ask user to login and send new token via Telegram."""
    STATE["waiting_for_token"] = True
    tg_send(
        "🔒 <b>TTD Session Expired!</b>\n\n"
        "Please do these steps on your iPhone:\n\n"
        "1️⃣ Open the Token Helper page:\n"
        "<a href='YOUR_HELPER_PAGE_URL_HERE'>👉 TTD Token Helper</a>\n\n"
        "2️⃣ Tap <b>Open TTD Login</b> and complete OTP\n\n"
        "3️⃣ Come back to the helper page\n\n"
        "4️⃣ Tap <b>Extract My Token</b> → <b>Copy Token</b>\n\n"
        "5️⃣ Paste it here as a reply ⬇️\n\n"
        "⏳ Waiting for your token..."
    )
    log.warning("Session expired — waiting for new token from Telegram.")


# ============================================================
# MAIN LOOP
# ============================================================

def main():
    log.info("TTD Slot Monitor starting...")
    log.info(f"Checking every {CHECK_INTERVAL // 60} minutes")
    log.info("Send /status to your Telegram bot anytime to check status")

    tg_send(
        "🚀 <b>TTD Slot Monitor Started!</b>\n\n"
        f"Checking every {CHECK_INTERVAL // 60} minutes.\n"
        "I'll alert you the moment slots open! 🙏\n\n"
        "Send /status anytime to check current status."
    )

    check_count = 0
    last_slot_alert = None  # Avoid spamming same alert

    while STATE["running"]:

        # Always check Telegram for replies
        process_telegram_messages()

        # If waiting for new token, just keep polling Telegram
        if STATE["waiting_for_token"]:
            time.sleep(5)
            continue

        # Check session validity
        valid, mins_remaining = is_session_valid()

        if not valid:
            request_new_token()
            # Poll Telegram every 5 seconds while waiting
            while STATE["waiting_for_token"] and STATE["running"]:
                process_telegram_messages()
                time.sleep(5)
            continue

        # Warn if session expiring soon
        if mins_remaining <= EXPIRY_WARNING // 60:
            log.warning(f"Session expires in {mins_remaining} mins!")
            if mins_remaining <= 5:
                request_new_token()
                while STATE["waiting_for_token"] and STATE["running"]:
                    process_telegram_messages()
                    time.sleep(5)
                continue

        # Check slots
        check_count += 1
        log.info(f"Check #{check_count} — Session valid for {mins_remaining} more mins")

        available = check_slots()

        if available is None:
            # Auth error from API
            log.warning("Auth error from API — requesting new token")
            request_new_token()
            while STATE["waiting_for_token"] and STATE["running"]:
                process_telegram_messages()
                time.sleep(5)
            continue

        if available:
            # Build alert message
            lines = "\n".join([f"📅 {d} — {n} slot(s)" for d, n in available])
            alert_key = lines  # Use content as key to avoid duplicate alerts

            if alert_key != last_slot_alert:
                last_slot_alert = alert_key
                log.info(f"SLOTS FOUND: {available}")
                tg_send(
                    f"🎉 <b>TTD Slots Available!</b>\n\n"
                    f"{lines}\n\n"
                    f"👉 <a href='{BOOKING_URL}'>Book NOW!</a>"
                )
            else:
                log.info(f"Slots still available (already alerted): {len(available)} dates")
        else:
            log.info("No slots available yet.")
            last_slot_alert = None  # Reset so we alert fresh next time slots appear

        # Wait before next check (but keep polling Telegram)
        for _ in range(CHECK_INTERVAL // 5):
            if not STATE["running"]:
                break
            process_telegram_messages()
            time.sleep(5)

    log.info("Monitor stopped.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("Stopped by user.")
