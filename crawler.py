#!/usr/bin/env python3
"""Pixnet blog crawler — downloads all posts and original images."""

import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse, parse_qs

import httpx
from bs4 import BeautifulSoup
from markdownify import markdownify as md
from tqdm import tqdm

BLOG_ROOT = "https://slfamily123.pixnet.net/blog"
OUTPUT = Path("output")
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    )
}
DELAY = 0.3

IMAGE_DOMAINS = {"pic.pimg.tw", "pimg.1px.tw"}
SKIP_PATTERNS = re.compile(
    r"(s\.pixfs\.net|fonts\.gstatic\.com|s3\.1px\.tw/blog/common)"
)
THUMB_SUFFIX = re.compile(r"(_[nmtso])(?=\.\w+$)")
PIXNET_EMOJI = re.compile(r"//s\.pixfs\.net/f\.pixnet\.net/images/emotions/")


def safe_filename(s: str, max_len: int = 80) -> str:
    s = re.sub(r'[<>:"/\\|?*]', "_", s)
    s = re.sub(r"\s+", " ", s).strip()
    if len(s) > max_len:
        s = s[:max_len]
    return s


def post_dir_name(article_or_meta: dict) -> str:
    if "date_published" in article_or_meta:
        date_str = article_or_meta["date_published"][:10]
    else:
        date_str = datetime.strptime(article_or_meta["date"], "%Y %b %d").strftime("%Y-%m-%d")
    title_slug = safe_filename(article_or_meta["title"])
    return f"{date_str}_{title_slug}"


def original_image_url(url: str) -> str:
    if "pic.pimg.tw" in url:
        url = THUMB_SUFFIX.sub("", url)
    return url


def is_skip_image(src: str) -> bool:
    return bool(SKIP_PATTERNS.search(src)) or bool(PIXNET_EMOJI.search(src))


class PixnetCrawler:
    def __init__(self):
        self.client = httpx.Client(
            headers=HEADERS, follow_redirects=True, timeout=30
        )
        self.articles: list[dict] = []
        self.image_count = 0
        self.skip_count = 0

    # ── Listing pages ──────────────────────────────────────────

    def discover_total_pages(self) -> int:
        resp = self.client.get(BLOG_ROOT)
        resp.encoding = "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")
        last_link = soup.find("a", string="最後一頁")
        if last_link and last_link.get("href"):
            qs = parse_qs(urlparse(last_link["href"]).query)
            return int(qs.get("page", [29])[0])
        return 29

    def collect_articles(self) -> list[dict]:
        total = self.discover_total_pages()
        seen: set[str] = set()
        articles: list[dict] = []

        for page in range(1, total + 1):
            url = f"{BLOG_ROOT}?page={page}" if page > 1 else BLOG_ROOT
            resp = self.client.get(url)
            resp.encoding = "utf-8"
            soup = BeautifulSoup(resp.text, "html.parser")

            for div in soup.select("div.article[id^=article-]"):
                post_id = div["id"].removeprefix("article-")
                if post_id in seen:
                    continue
                seen.add(post_id)

                title_el = div.select_one(".title h2 a")
                title = title_el.get_text(strip=True) if title_el else ""

                href = title_el["href"] if title_el else f"{BLOG_ROOT}/posts/{post_id}"

                pub = div.select_one(".publish")
                date_str = ""
                if pub:
                    parts = [
                        pub.select_one(".year"),
                        pub.select_one(".month"),
                        pub.select_one(".date"),
                    ]
                    date_str = " ".join(
                        p.get_text(strip=True) for p in parts if p
                    )

                cat_el = div.select_one(".article-footer .refer li a")
                category = cat_el.get_text(strip=True) if cat_el else ""

                articles.append(
                    {
                        "post_id": post_id,
                        "title": title,
                        "url": urljoin(BLOG_ROOT, href),
                        "date": date_str,
                        "category": category,
                    }
                )

            time.sleep(DELAY)

        self.articles = articles
        return articles

    # ── Single article ─────────────────────────────────────────

    def fetch_article(self, article: dict) -> str | None:
        for attempt in range(3):
            try:
                resp = self.client.get(article["url"])
                resp.encoding = "utf-8"
                return resp.text
            except Exception as e:
                print(f"  Retry {attempt+1}: {e}")
                time.sleep(DELAY * 2)
        return None

    def parse_article(self, html: str, article: dict) -> dict:
        soup = BeautifulSoup(html, "html.parser")

        meta = self._extract_meta(soup, article)
        content_html = self._extract_content_html(soup)
        images = self._extract_images(content_html)
        md_body = self._html_to_markdown(content_html)

        meta["content_html"] = content_html
        meta["content_md"] = md_body
        meta["images"] = images
        return meta

    def _extract_meta(self, soup: BeautifulSoup, fallback: dict) -> dict:
        ld = soup.find("script", type="application/ld+json")
        structured = {}
        if ld:
            try:
                data = json.loads(ld.string)
                if isinstance(data, dict):
                    structured = data
            except (json.JSONDecodeError, TypeError):
                pass

        title = (
            structured.get("headline")
            or soup.select_one("meta[property='og:title']")
            and soup.select_one("meta[property='og:title']")["content"]
            or fallback["title"]
        )
        description = structured.get("description") or (
            soup.select_one("meta[name='description']")
            and soup.select_one("meta[name='description']")["content"]
            or ""
        )
        date_pub = structured.get("datePublished") or ""
        date_mod = structured.get("dateModified") or ""
        category = structured.get("articleSection") or fallback.get("category", "")

        return {
            "post_id": fallback["post_id"],
            "title": title,
            "url": fallback["url"],
            "description": description,
            "date_published": date_pub,
            "date_modified": date_mod,
            "category": category,
        }

    def _extract_content_html(self, soup: BeautifulSoup) -> str:
        container = soup.select_one("#article-content-inner")
        if container:
            for tag in container.find_all(
                ["script", "iframe", "noscript", "ins", "style"]
            ):
                tag.decompose()
            return str(container)
        return ""

    def _extract_images(self, html: str) -> list[dict]:
        soup = BeautifulSoup(html, "html.parser")
        images = []
        for img in soup.find_all("img"):
            src = img.get("src", "")
            if not src or is_skip_image(src):
                continue
            if not any(d in src for d in IMAGE_DOMAINS):
                continue
            original = original_image_url(src)
            alt = img.get("alt", "") or ""
            images.append({"src": src, "original": original, "alt": alt})
        return images

    def _html_to_markdown(self, html: str) -> str:
        if not html:
            return ""
        heading_style = "ATX"
        body = md(
            html,
            heading_style=heading_style,
            strip=["script", "iframe", "noscript", "ins", "style"],
        )
        body = re.sub(r"\n{3,}", "\n\n", body)
        return body.strip()

    # ── Image download ─────────────────────────────────────────

    def download_images(
        self, images: list[dict], img_dir: Path
    ) -> list[dict]:
        img_dir.mkdir(parents=True, exist_ok=True)
        results = []
        for img in images:
            url = img["original"]
            fname = Path(urlparse(url).path).name
            if not fname:
                continue
            local = img_dir / fname
            if local.exists():
                results.append(
                    {"original_url": url, "local": str(local.relative_to(OUTPUT)), "alt": img["alt"]}
                )
                continue
            for attempt in range(2):
                try:
                    r = self.client.get(url, timeout=30)
                    if r.status_code == 200:
                        local.write_bytes(r.content)
                        self.image_count += 1
                        results.append(
                            {
                                "original_url": url,
                                "local": str(local.relative_to(OUTPUT)),
                                "alt": img["alt"],
                            }
                        )
                        break
                    elif r.status_code == 404 and "pic.pimg.tw" in url:
                        fallback = img["src"]
                        if fallback != url:
                            r2 = self.client.get(fallback, timeout=30)
                            if r2.status_code == 200:
                                local.write_bytes(r2.content)
                                self.image_count += 1
                                results.append(
                                    {
                                        "original_url": fallback,
                                        "local": str(
                                            local.relative_to(OUTPUT)
                                        ),
                                        "alt": img["alt"],
                                    }
                                )
                                break
                except Exception as e:
                    print(f"    Image DL failed ({url}): {e}")
            else:
                self.skip_count += 1
        return results

    # ── Save ───────────────────────────────────────────────────

    def save_article(self, meta: dict, downloaded: list[dict], orig_images: list[dict] | None = None):
        post_dir = OUTPUT / "posts" / post_dir_name(meta)
        post_dir.mkdir(parents=True, exist_ok=True)

        (post_dir / "meta.json").write_text(
            json.dumps(
                {k: v for k, v in meta.items() if k not in ("content_html", "content_md", "images")},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        md_text = meta["content_md"]
        md_text = self._replace_image_refs(md_text, orig_images or [], downloaded)
        (post_dir / "post.md").write_text(md_text, encoding="utf-8")

        if downloaded:
            (post_dir / "images.json").write_text(
                json.dumps(downloaded, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    def _replace_image_refs(
        self, md_text: str, orig_images: list[dict], downloaded: list[dict]
    ) -> str:
        url_to_local = {d["original_url"]: d["local"] for d in downloaded}
        url_to_local.update({d["src"]: d["local"] for d in downloaded if d.get("src")})
        for img in orig_images:
            local = url_to_local.get(img["original"]) or url_to_local.get(img["src"])
            if local:
                rel = f"images/{Path(local).name}"
                md_text = md_text.replace(img["original"], rel)
                if img["src"] != img["original"]:
                    md_text = md_text.replace(img["src"], rel)
        return md_text

    # ── Index ──────────────────────────────────────────────────

    def generate_index(self):
        all_posts = []
        for article in self.articles:
            pdir = post_dir_name(article)
            all_posts.append(
                {
                    "post_id": article["post_id"],
                    "title": article["title"],
                    "url": article["url"],
                    "date": article["date"],
                    "category": article.get("category", ""),
                    "local": f"posts/{pdir}",
                }
            )

        (OUTPUT / "all_posts.json").write_text(
            json.dumps(all_posts, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        lines = ["# SL的家庭生活 — 全站文章索引\n"]
        for p in all_posts:
            title = p["title"]
            date = p["date"]
            link = f"{p['local']}/"
            lines.append(f"- {date} — [{title}]({link})")
        (OUTPUT / "index.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _article_exists(self, article: dict) -> bool:
        post_dir = OUTPUT / "posts" / post_dir_name(article)
        return (post_dir / "post.md").exists()

    # ── Main ───────────────────────────────────────────────────

    def run(self):
        print("=== Step 1: Collecting article list ===")
        articles = self.collect_articles()
        print(f"  Found {len(articles)} articles\n")

        print(f"=== Step 2: Downloading articles & images ({len(articles)} total) ===")
        for i, article in enumerate(tqdm(articles, unit="article"), 1):
            if self._article_exists(article):
                continue
            html = self.fetch_article(article)
            if not html:
                print(f"  FAILED to fetch: {article['title']}")
                continue
            meta = self.parse_article(html, article)
            orig_images = meta.get("images", [])
            downloaded = []
            if orig_images:
                img_dir = OUTPUT / "posts" / post_dir_name(meta) / "images"
                downloaded = self.download_images(orig_images, img_dir)
            self.save_article(meta, downloaded, orig_images)
            time.sleep(DELAY)

        print(f"\n=== Done ===")
        print(f"  Articles: {len(articles)}")
        print(f"  Images downloaded: {self.image_count}")
        print(f"  Images skipped (404): {self.skip_count}")
        print(f"  Output: {OUTPUT.resolve()}")

        print("\n=== Step 3: Generating index ===")
        self.generate_index()
        print("  Index written to output/index.md and output/all_posts.json")


if __name__ == "__main__":
    PixnetCrawler().run()
