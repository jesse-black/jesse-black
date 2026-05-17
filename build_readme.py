#!/usr/bin/env python3
"""Update the profile README with recent blog posts and GitHub releases."""

from __future__ import annotations

import datetime as dt
import email.utils
import html
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Iterable


GITHUB_USER = "jesse-black"
BLOG_FEED_URL = "https://jesseblack.net/rss.xml"
README_PATH = "README.md"
BLOG_LIMIT = 5
RELEASE_LIMIT = 5
EXCLUDED_BLOG_RELEASE_REPO = "jesseblack.net"
EXCLUDED_BLOG_RELEASE_TAG_PREFIX = "post/"
DESCRIPTION_LIMIT = 240


@dataclass(frozen=True)
class BlogPost:
    title: str
    url: str
    published: dt.datetime
    description: str | None = None


@dataclass(frozen=True)
class Release:
    repo: str
    version: str
    url: str
    published: dt.datetime


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def parse_datetime(value: str | None) -> dt.datetime:
    if not value:
        return dt.datetime.min.replace(tzinfo=dt.timezone.utc)

    value = value.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"

    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError:
        parsed_tuple = email.utils.parsedate_to_datetime(value)
        parsed = parsed_tuple

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def fetch_url(url: str, *, headers: dict[str, str] | None = None) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "jesse-black-profile-readme-updater",
            **(headers or {}),
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def fetch_blog_posts() -> list[BlogPost]:
    document = ET.fromstring(fetch_url(BLOG_FEED_URL))
    posts: list[BlogPost] = []

    for item in document.findall("./channel/item"):
        title = text_of(item, "title")
        link = text_of(item, "link")
        published = text_of(item, "pubDate")
        description = summarize_description(text_of(item, "description"))
        if not title or not link:
            continue
        posts.append(
            BlogPost(
                title=html.unescape(title),
                url=link,
                published=parse_datetime(published),
                description=description,
            )
        )

    return sorted(posts, key=lambda post: post.published, reverse=True)[:BLOG_LIMIT]


def text_of(element: ET.Element, tag: str) -> str:
    child = element.find(tag)
    if child is None or child.text is None:
        return ""
    return child.text.strip()


def summarize_description(value: str) -> str:
    description = re.sub(r"<[^>]+>", " ", html.unescape(value))
    description = re.sub(r"\s+", " ", description).strip()
    if len(description) <= DESCRIPTION_LIMIT:
        return description
    return description[: DESCRIPTION_LIMIT - 1].rsplit(" ", 1)[0] + "..."


def github_graphql(query: str, variables: dict[str, object]) -> dict[str, object]:
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("GITHUB_TOKEN is required to fetch GitHub releases")

    payload = json.dumps({"query": query, "variables": variables}).encode()
    request = urllib.request.Request(
        "https://api.github.com/graphql",
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "jesse-black-profile-readme-updater",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.loads(response.read())
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub GraphQL request failed: {error.code} {body}") from error

    if data.get("errors"):
        raise RuntimeError(f"GitHub GraphQL errors: {data['errors']}")
    return data["data"]


def fetch_releases() -> list[Release]:
    query = """
    query LatestReleases($login: String!, $after: String) {
      user(login: $login) {
        repositories(
          first: 100
          after: $after
          privacy: PUBLIC
          ownerAffiliations: OWNER
          orderBy: {field: PUSHED_AT, direction: DESC}
        ) {
          pageInfo {
            hasNextPage
            endCursor
          }
          nodes {
            name
            releases(first: 5, orderBy: {field: CREATED_AT, direction: DESC}) {
              nodes {
                name
                tagName
                publishedAt
                url
              }
            }
          }
        }
      }
    }
    """
    releases: list[Release] = []
    cursor: str | None = None

    while True:
        data = github_graphql(query, {"login": GITHUB_USER, "after": cursor})
        repositories = data["user"]["repositories"]
        for repo in repositories["nodes"]:
            repo_name = repo["name"]
            for release in repo["releases"]["nodes"]:
                if is_blog_release(repo_name, release.get("tagName") or ""):
                    continue
                version = release.get("tagName") or release.get("name")
                if not version:
                    continue
                published = parse_datetime(release.get("publishedAt"))
                releases.append(
                    Release(
                        repo=repo_name,
                        version=version,
                        url=release["url"],
                        published=published,
                    )
                )

        page_info = repositories["pageInfo"]
        if not page_info["hasNextPage"]:
            break
        cursor = page_info["endCursor"]

    return sorted(releases, key=lambda release: release.published, reverse=True)[:RELEASE_LIMIT]


def is_blog_release(repo_name: str, tag_name: str) -> bool:
    decoded_tag = urllib.parse.unquote(tag_name)
    return (
        repo_name == EXCLUDED_BLOG_RELEASE_REPO
        and decoded_tag.startswith(EXCLUDED_BLOG_RELEASE_TAG_PREFIX)
    )


def render_releases(items: Iterable[Release]) -> str:
    lines: list[str] = []
    for release in items:
        date = release.published.strftime("%Y-%m-%d")
        title = html.escape(f"{release.repo} {release.version}")
        url = html.escape(release.url, quote=True)
        lines.append(f'<li><a href="{url}">{title}</a><br><small>{date}</small></li>')
    return render_list(lines)


def render_blog_posts(items: Iterable[BlogPost]) -> str:
    lines: list[str] = []
    for post in items:
        date = post.published.strftime("%Y-%m-%d")
        title = html.escape(post.title)
        url = html.escape(post.url, quote=True)
        description = html.escape(post.description or "")
        description_html = f"<br>{description}" if description else ""
        lines.append(
            f'<li><a href="{url}">{title}</a><br><small>{date}</small>{description_html}</li>'
        )
    return render_list(lines)


def render_list(items: list[str]) -> str:
    if not items:
        return "<p><em>No recent items found.</em></p>"
    return "<ul>\n" + "\n".join(items) + "\n</ul>"


def replace_block(readme: str, name: str, content: str) -> str:
    pattern = re.compile(
        rf"<!-- {re.escape(name)} starts -->.*?<!-- {re.escape(name)} ends -->",
        re.DOTALL,
    )
    replacement = f"<!-- {name} starts -->\n{content}\n<!-- {name} ends -->"
    updated, count = pattern.subn(replacement, readme)
    if count != 1:
        raise RuntimeError(f"Could not find exactly one README block named {name!r}")
    return updated


def main() -> int:
    with open(README_PATH, encoding="utf-8") as readme_file:
        readme = readme_file.read()

    updated = replace_block(readme, "blog", render_blog_posts(fetch_blog_posts()))
    updated = replace_block(updated, "releases", render_releases(fetch_releases()))
    updated = re.sub(
        r"Last updated automatically by GitHub Actions(?: on \d{4}-\d{2}-\d{2})?\.",
        f"Last updated automatically by GitHub Actions on {utc_now():%Y-%m-%d}.",
        updated,
    )

    if updated != readme:
        with open(README_PATH, "w", encoding="utf-8") as readme_file:
            readme_file.write(updated)

    return 0


if __name__ == "__main__":
    sys.exit(main())
