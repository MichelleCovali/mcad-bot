import json
import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

from filters import matching_keywords
from news import fetch_articles


PROJECT_DIR = Path(__file__).resolve().parent
HISTORY_FILE = PROJECT_DIR / "sent_links.json"

load_dotenv(PROJECT_DIR / ".env")


def send_message(text):
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()

    if not token or not chat_id:
        print("Missing Telegram token or chat ID in .env.")
        return False

    url = "https://" + "api.telegram.org" + "/bot" + token + "/sendMessage"

    try:
        response = requests.post(
            url,
            json={"chat_id": chat_id, "text": text},
            timeout=20,
        )
        data = response.json()

        if not data.get("ok"):
            print("Telegram could not send the message.")
            return False

        return True

    except requests.RequestException:
        print("Could not connect to Telegram.")
        return False
    except ValueError:
        print("Telegram returned an unexpected response.")
        return False


def load_sent_links():
    if not HISTORY_FILE.exists():
        return set()

    data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))

    if not isinstance(data, list) or not all(
        isinstance(link, str) for link in data
    ):
        raise ValueError("Invalid history format")

    return set(data)


def save_sent_links(links):
    temporary_file = HISTORY_FILE.with_suffix(".tmp")
    temporary_file.write_text(
        json.dumps(sorted(links), indent=2),
        encoding="utf-8",
    )
    temporary_file.replace(HISTORY_FILE)


def main():
    try:
        sent_links = load_sent_links()
    except (OSError, ValueError):
        print("Could not read sending history. Stopping to avoid repeats.")
        return

    articles = fetch_articles()
    sent_count = 0

    for article in articles:
        link = article["link"]

        if not matching_keywords(article) or link in sent_links:
            continue

        if not send_message(link):
            print("Stopping. The link was not marked as sent.")
            break

        sent_links.add(link)

        try:
            save_sent_links(sent_links)
        except OSError:
            print("Message sent, but history could not be saved. Stopping.")
            return

        sent_count += 1

    print(f"Checked {len(articles)} articles.")
    print(f"Sent {sent_count} new links.")


if __name__ == "__main__":
    print("MCAD bot started. Checking every 15 minutes.")
    print("Press Control + C to stop.")

    try:
        while True:
            main()
            print("Waiting 15 minutes before the next check...")
            time.sleep(15 * 60)
    except KeyboardInterrupt:
        print("\nMCAD bot stopped.")