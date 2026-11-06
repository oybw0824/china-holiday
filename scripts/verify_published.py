#!/usr/bin/env python3
"""Verify the public repository, branch Pages build and exact fixed HTTPS feed."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
from urllib.request import Request, urlopen

from holiday_calendar import ROOT, FEEDS, load_years, require, validate_ics


def github(path):
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "ChinaHolidayCalendar/1.0", "X-GitHub-Api-Version": "2022-11-28"}
    token = os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = "Bearer " + token
    with urlopen(Request("https://api.github.com/" + path, headers=headers), timeout=25) as response:
        require(response.status == 200, "GitHub metadata did not return HTTP 200")
        return json.load(response)


def public_get(url):
    with urlopen(Request(url, headers={"User-Agent": "ChinaHolidayCalendar/1.0", "Cache-Control": "no-cache"}), timeout=25) as response:
        require(response.status == 200 and response.url == url, "Fixed HTTPS endpoint did not return direct HTTP 200")
        raw = response.read(10_000_001)
        require(len(raw) <= 10_000_000, "Published response is too large")
        return raw


def verify(repository, commit, root):
    require(re.fullmatch(r"[A-Za-z0-9_.-]+/china-holiday", repository) is not None, "Unexpected repository name")
    repo = github("repos/" + repository)
    require(repo["private"] is False and repo["default_branch"] == "main", "Repository must be public with main as default branch")
    pages = github("repos/" + repository + "/pages")
    require(pages.get("source") == {"branch": "main", "path": "/"}, "Pages must publish main at repository root")
    require(pages.get("build_type", "legacy") == "legacy", "Pages source must remain branch-based")
    build = github("repos/" + repository + "/pages/builds/latest")
    require(build["status"] == "built" and not build.get("error", {}).get("message"), "Latest Pages build has not succeeded")
    if commit:
        require(build["commit"] == commit, "Pages has not built the expected commit")
    owner = repo["owner"]["login"].lower()
    site = f"https://{owner}.github.io/china-holiday/"
    require(pages["html_url"].rstrip("/").lower() == site.rstrip("/").lower(), "Pages URL differs from the required fixed subscription host/path")
    public_get(site)
    datasets = load_years(root)
    feeds = {}
    for name, (kind, _) in FEEDS.items():
        url = site + name
        raw = public_get(url)
        events = validate_ics(raw, datasets, kind)
        require(raw == (root / name).read_bytes(), f"Published {name} is stale or differs from validated repository content")
        feeds[name] = {"url": url, "http_status": 200, "events": len(events), "sha256": hashlib.sha256(raw).hexdigest()}
    return {"repository": repo["html_url"], "public": True, "website": site, "subscription": site + "holiday.ics", "format": "VCALENDAR 2.0", "feeds": feeds, "pages_commit": build["commit"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--expected-commit")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--wait-seconds", type=int, default=0)
    args = parser.parse_args()
    deadline = time.monotonic() + args.wait_seconds
    while True:
        try:
            result = verify(args.repository, args.expected_commit, args.root)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        except (OSError, ValueError, KeyError, TypeError) as exc:
            if time.monotonic() >= deadline:
                print("Publication verification failed: " + str(exc), file=sys.stderr)
                return 1
            print("Waiting for expected Pages build and HTTPS calendar...", flush=True)
            time.sleep(10)


if __name__ == "__main__":
    raise SystemExit(main())
