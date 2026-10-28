import re
from html import unescape

from filters import matching_keywords

# Same incident words as archive.py, applied to RSS title and summary.
INCIDENT_WORDS = (
    "cyberattack",
    "cyber attack",
    "ransomware",
    "breach",
    "hacked",
    "data theft",
)


def article_text(article):
    text = article.get("title", "") + " " + article.get("summary", "")
    return unescape(re.sub(r"<[^>]+>", " ", text))


def matching_incident_words(article):
    """Return incident words found in the title or summary."""
    text = article_text(article)

    return [
        word
        for word in INCIDENT_WORDS
        if re.search(r"\b" + re.escape(word) + r"\b", text, re.IGNORECASE)
    ]


def classify_article(article):
    """Split keyword evidence into maritime and incident lists."""
    return {
        "maritime": matching_keywords(article),
        "incident": matching_incident_words(article),
    }


def is_dual_evidence(classification):
    return bool(classification["maritime"] and classification["incident"])


if __name__ == "__main__":
    example = {
        "title": "Ransomware disrupts Port of Example",
        "summary": "A fictional maritime cyberattack for local testing.",
    }
    result = classify_article(example)
    print("Test classification:", result)
    print("Dual evidence:", is_dual_evidence(result))
    print()

    from news import fetch_articles

    articles = fetch_articles()
    dual = 0
    maritime_only = 0

    for article in articles:
        classification = classify_article(article)
        maritime = classification["maritime"]
        incident = classification["incident"]

        if not maritime:
            continue

        if incident:
            dual += 1
            label = "dual"
        else:
            maritime_only += 1
            label = "maritime-only"

        print(article["title"])
        print(article["link"])
        print("Type:", label)
        print("Maritime:", ", ".join(maritime) or "-")
        print("Incident:", ", ".join(incident) or "-")
        print()

    print(f"Checked {len(articles)} articles.")
    print(f"Found {dual} dual-evidence reports.")
    print(f"Found {maritime_only} maritime-only reports.")
