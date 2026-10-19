import os
import time

import requests

from archive import fetch_archive_reports
from bot import send_message


def handle_message(message):
    chat = message.get("chat", {})

    # For this prototype, respond only in private conversations.
    if chat.get("type") != "private":
        return

    text = message.get("text", "").strip()
    command = text.split()[0].lower() if text else ""
    command = command.split("@")[0]
    chat_id = chat["id"]

    if command == "/latest":
        reports = fetch_archive_reports()

        if not reports:
            send_message(
                "Could not find a report in the port archive. "
                "The archive may be unavailable or its layout may have changed.",
                chat_id,
            )
            return

        # The archive displays reports newest first.
        report = reports[0]
        reply = (
            "Latest matching report in BleepingComputer's port archive\n\n"
            + report["title"]
            + "\n\n"
            + report["link"]
            + "\n\nThis may describe an older incident or an update. "
            "Human verification is required."
        )
        send_message(reply, chat_id)

    elif command in ("/start", "/help"):
        send_message(
            "Send /latest to get the newest matching incident report "
            "listed in BleepingComputer's port archive.",
            chat_id,
        )


def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

    if not token:
        print("Missing TELEGRAM_BOT_TOKEN in .env.")
        return

    url = "https://" + "api.telegram.org" + "/bot" + token + "/getUpdates"
    offset = 0

    print("Listening for Telegram commands.")
    print("Send /latest to your bot. Press Control + C to stop.")

    while True:
        try:
            response = requests.get(
                url,
                params={"offset": offset, "timeout": 25},
                timeout=35,
            )
            data = response.json()

            if not data.get("ok"):
                print("Telegram rejected the request.")
                print("Check the token and stop any other command listeners.")
                return

            for update in data.get("result", []):
                if "message" in update:
                    handle_message(update["message"])

                offset = update["update_id"] + 1

        except requests.RequestException:
            print("Connection interrupted. Retrying in 10 seconds.")
            time.sleep(10)
        except ValueError:
            print("Unexpected response. Retrying in 10 seconds.")
            time.sleep(10)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nCommand listener stopped.")