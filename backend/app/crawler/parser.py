import trafilatura
from bs4 import BeautifulSoup

from app.crawler.models import Link, ParsedPage
from app.crawler.normalizer import InvalidURL, hostname, normalize_url


def parse_page(html: bytes, final_url: str, root_domain: str) -> ParsedPage:
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else None
    description = soup.find(
        "meta", attrs={"name": lambda value: value and value.lower() == "description"}
    )
    description_text = description.get("content") if description else None
    canonical = soup.find("link", attrs={"rel": lambda value: value and "canonical" in value})
    canonical_url = None
    if canonical and canonical.get("href"):
        try:
            canonical_url = normalize_url(canonical["href"], final_url)
        except InvalidURL:
            pass
    links: list[Link] = []
    for anchor in soup.find_all("a", href=True):
        raw = anchor["href"].strip()
        try:
            target = normalize_url(raw, final_url)
        except InvalidURL:
            continue
        nearby = anchor.find_parent(["p", "li"])
        if nearby is None:
            nearby = anchor.parent
        context = " ".join(nearby.get_text(" ", strip=True).split()) if nearby else ""
        links.append(
            Link(
                raw,
                target,
                anchor.get_text(" ", strip=True)[:500],
                hostname(target) == root_domain,
                context[:320],
            )
        )
    for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()
    extracted = trafilatura.extract(str(soup), include_comments=False)
    if extracted is None:
        main = soup.find("main") or soup.find("article") or soup.body or soup
        extracted = main.get_text(" ", strip=True)
    return ParsedPage(title, extracted.strip(), description_text, canonical_url, links)
