from evidence import classify_article, is_dual_evidence


MAX_LISTED = 5
MAX_TELEGRAM_CHARS = 3900


def format_entry(index, article, classification):
    maritime = ", ".join(classification["maritime"]) or "-"
    incident = ", ".join(classification["incident"]) or "-"
    title = article.get("title", "").strip() or "(untitled)"
    link = article.get("link", "").strip()

    return (
        f"{index}. {title}\n"
        f"Maritime: {maritime}\n"
        f"Incident: {incident}\n"
        f"{link}"
    )


def build_digest(articles):
    dual = []
    maritime_only = []

    for article in articles:
        classification = classify_article(article)

        if not classification["maritime"]:
            continue

        row = (article, classification)
        if is_dual_evidence(classification):
            dual.append(row)
        else:
            maritime_only.append(row)

    lines = [
        "MCAD review digest",
        "",
        f"Checked {len(articles)} recent BleepingComputer articles.",
        (
            f"Dual-evidence candidates: {len(dual)}. "
            f"Maritime-only (current alert rule): {len(maritime_only)}."
        ),
        "",
        "Dual-evidence means the title or summary contains both a "
        "maritime keyword and an incident word. Human review is still "
        "required before inclusion in MCAD.",
    ]

    lines.append("")
    lines.append("Dual-evidence candidates")

    if dual:
        for index, (article, classification) in enumerate(dual[:MAX_LISTED], start=1):
            lines.append("")
            lines.append(format_entry(index, article, classification))
        extra = len(dual) - MAX_LISTED
        if extra > 0:
            lines.append("")
            lines.append(f"{extra} more dual-evidence item(s) not listed.")
    else:
        lines.append("")
        lines.append("None in the current RSS window.")

    lines.append("")
    lines.append("Maritime-only (would match bot.py, weaker evidence)")

    if maritime_only:
        for index, (article, classification) in enumerate(
            maritime_only[:MAX_LISTED], start=1
        ):
            lines.append("")
            lines.append(format_entry(index, article, classification))
        extra = len(maritime_only) - MAX_LISTED
        if extra > 0:
            lines.append("")
            lines.append(f"{extra} more maritime-only item(s) not listed.")
    else:
        lines.append("")
        lines.append("None in the current RSS window.")

    text = "\n".join(lines)
    if len(text) > MAX_TELEGRAM_CHARS:
        text = text[: MAX_TELEGRAM_CHARS - 20].rstrip() + "\n\n[truncated]"

    return text, len(dual), len(maritime_only)


def main():
    from bot import send_message
    from news import fetch_articles

    articles = fetch_articles()
    text, dual_count, maritime_only_count = build_digest(articles)

    print(text)
    print()

    if send_message(text):
        print("Digest sent to Telegram.")
    else:
        print("Digest was not sent. Check .env and the terminal output above.")

    print(
        f"Summary: {len(articles)} articles, "
        f"{dual_count} dual-evidence, "
        f"{maritime_only_count} maritime-only."
    )


if __name__ == "__main__":
    main()
