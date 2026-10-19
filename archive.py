from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup


BASE_URL = "https://" + "www.bleepingcomputer.com"
ARCHIVE_URL = BASE_URL + "/tag/port/"

INCIDENT_WORDS = (
    "cyberattack",
    "cyber attack",
    "ransomware",
    "breach",
    "hacked",
    "data theft",
)


def fetch_archive_reports():
    """Find possible incident reports in archive display order."""
    try:
        response = requests.get(
            ARCHIVE_URL,
            headers={"User-Agent": "MCAD-Student-Prototype/0.1"},
            timeout=30,
        )
        response.raise_for_status()
    except requests.RequestException:
        print("Could not retrieve the archive.")
        return []

    soup = BeautifulSoup(response.content, "html.parser")
    reports = []
    seen = set()

    for heading in soup.select("h4"):
        anchor = heading.find("a", href=True)

        if anchor is None:
            continue

        title = anchor.get_text(" ", strip=True)
        link = urljoin(BASE_URL, anchor["href"])
        parsed = urlparse(link)

        # Ignore advertisements and links outside security news.
        if (
            parsed.hostname != "www.bleepingcomputer.com"
            or not parsed.path.startswith("/news/security/")
        ):
            continue

        if not any(word in title.lower() for word in INCIDENT_WORDS):
            continue

        if link in seen:
            continue

        seen.add(link)
        reports.append({"title": title, "link": link})

    return reports


if __name__ == "__main__":
    reports = fetch_archive_reports()

    print(f"Found {len(reports)} possible incident reports.\n")

    for report in reports[:5]:
        print(report["title"])
        print(report["link"])
        print()