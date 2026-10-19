import os
from pathlib import Path

import requests
from dotenv import load_dotenv


# Read settings from the .env beside this file.
load_dotenv(Path(__file__).resolve().parent / ".env")


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


if __name__ == "__main__":
    if send_message("Your MCAD bot is running from VS Code!"):
        print("Message sent successfully.")