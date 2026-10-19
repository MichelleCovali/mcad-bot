import re
from html import unescape


MARITIME_KEYWORDS = [
    "maritime",
    "seaport",
    "shipping",
    "shipyard",
    "vessel",
    "port authority",
    "ports authority",
    "port of",
    "container terminal",
    "DP World",
    "Maersk",
]


def matching_keywords(article):
    """Return maritime keywords found in the title or summary."""
    text = article.get("title", "") + " " + article.get("summary", "")
    text = unescape(re.sub(r"<[^>]+>", " ", text))

    return [
        keyword
        for keyword in MARITIME_KEYWORDS
        if re.search(
            r"\b" + re.escape(keyword) + r"\b",
            text,
            re.IGNORECASE,
        )
    ]


if __name__ == "__main__":
    from news import fetch_articles

    example = {
        "title": "Cyberattack disrupts Port of Example",
        "summary": "A fictional report for testing the filter.",
    }
    print("Test match:", matching_keywords(example))
    print()

    articles = fetch_articles()
    matches = 0

    for article in articles:
        keywords = matching_keywords(article)

        if keywords:
            matches += 1
            print(article["title"])
            print(article["link"])
            print("Matched:", ", ".join(keywords))
            print()

    print(f"Checked {len(articles)} articles.")
    print(f"Found {matches} possible maritime reports.")