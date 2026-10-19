import requests
import feedparser


FEED_URL = "https://" + "www.bleepingcomputer.com" + "/feed/"


def fetch_articles():
    """Read the latest articles from BleepingComputer."""
    try:
        response = requests.get(
            FEED_URL,
            headers={"User-Agent": "MCAD-Student-Prototype/0.1"},
            timeout=30,
        )
        response.raise_for_status()

    except requests.RequestException:
        print("Could not retrieve the news feed.")
        return []

    feed = feedparser.parse(response.content)

    if not feed.entries:
        print("The feed returned no articles.")
        return []

    return [
        {
            "title": entry.get("title", ""),
            "link": entry.get("link", ""),
            "summary": entry.get("summary", ""),
        }
        for entry in feed.entries
        if entry.get("link")
    ]


if __name__ == "__main__":
    articles = fetch_articles()
    print(f"Found {len(articles)} articles.\n")

    for article in articles[:5]:
        print(article["title"])
        print(article["link"])
        print()