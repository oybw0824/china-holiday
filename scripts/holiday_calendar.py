#!/usr/bin/env python3
"""Official-notice-only calendar maintenance; Python 3.12+, no runtime dependencies."""
from __future__ import annotations

import argparse
import copy
import hashlib
import html
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import uuid

ROOT = Path(__file__).resolve().parents[1]
FIRST_YEAR = 2027
HOLIDAY_NAMES = {"元旦", "春节", "清明节", "劳动节", "端午节", "中秋节", "国庆节"}
SEARCH_ENDPOINT = "https://sousuo.www.gov.cn/search-gov/data"
ARTICLE_PATH = re.compile(r"^/zhengce/(?:content|zhengceku)/\d{4}(?:-?\d{2}(?:/\d{2})?)/content_\d+\.htm$")
DATE_TOKEN = re.compile(r"(?:(\d{4})年)?(?:(\d{1,2})月)?(\d{1,2})日(（[^）]*）|\([^)]*\))?")


class InvalidData(ValueError):
    pass


def require(ok, message):
    if not ok:
        raise InvalidData(message)


def compact(text):
    return re.sub(r"\s+", "", html.unescape(text))


def official_url(url, *, search=False):
    parsed = urlsplit(url)
    hosts = {"www.gov.cn", "sousuo.www.gov.cn"} if search else {"www.gov.cn"}
    require(parsed.scheme == "https" and parsed.hostname in hosts, "Only HTTPS China Government sources are accepted")
    require(parsed.port in (None, 443) and not parsed.username and not parsed.password, "Invalid official URL")
    if not search:
        require(bool(ARTICLE_PATH.fullmatch(parsed.path)), "Not an official policy-notice article URL")
        require(not parsed.query and not parsed.fragment, "Article URL must not contain a query or fragment")
    return url


class OfficialRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        official_url(newurl, search=True)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url):
    official_url(url, search=True)
    last_error = None
    for attempt in range(3):
        try:
            request = Request(url, headers={"User-Agent": "ChinaHolidayCalendar/1.0 (+https://www.gov.cn/)", "Accept": "application/json,text/html"})
            with build_opener(OfficialRedirects()).open(request, timeout=30) as response:
                official_url(response.url, search=True)
                require(response.status == 200, "Official source did not return HTTP 200")
                data = response.read(4_000_001)
                require(len(data) <= 4_000_000, "Official response exceeds size limit")
                return data
        except InvalidData:
            raise
        except (OSError, ValueError) as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(2 ** attempt)
    raise InvalidData(f"Official source fetch failed: {last_error}")


class NoticeHTML(HTMLParser):
    """Capture only the official article body, never menus or related articles."""
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.capture = None
        self.parts = []
        self.found = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag not in self.VOID:
            self.stack.append(tag)
        if attrs.get("id") == "UCAP-CONTENT":
            self.found += 1
            self.capture = len(self.stack)
        if self.capture and tag in {"p", "div", "br"}:
            self.parts.append("\n")

    def handle_startendtag(self, tag, attrs):
        if self.capture and tag == "br":
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if not self.stack or tag not in self.stack:
            return
        if self.capture and tag in {"p", "div"}:
            self.parts.append("\n")
        index = len(self.stack) - 1 - self.stack[::-1].index(tag)
        if self.capture is not None and index < self.capture:
            self.capture = None
        self.stack = self.stack[:index]

    def handle_data(self, data):
        if self.capture and not ({"script", "style"} & set(self.stack)):
            self.parts.append(data)


def article_text(raw):
    parser = NoticeHTML()
    parser.feed(raw.decode("utf-8-sig", errors="strict"))
    require(parser.found == 1, "Official article body is missing or ambiguous; parser maintenance required")
    text = "\n".join(line.strip() for line in "".join(parser.parts).splitlines() if line.strip()) + "\n"
    require(bool(text.strip()), "Official article body is empty")
    return text


def parse_dates(text, year):
    result = []
    month = None
    active_year = year
    for token in DATE_TOKEN.finditer(text):
        yy, mm, dd, note = token.groups()
        active_year = int(yy) if yy else active_year
        month = int(mm) if mm else month
        require(month is not None, "Date has no explicit month")
        day = date(active_year, month, int(dd))
        if note:
            weekday = re.search(r"周([一二三四五六日天])", note)
            if weekday:
                expected = "一二三四五六日".index(weekday[1].replace("天", "日"))
                require(day.weekday() == expected, f"Weekday disagrees with official date: {day}")
        result.append(day)
    return result


def parse_notice(text, url, today=None):
    official_url(url)
    today = today or datetime.now(timezone.utc).date()
    text_compact = compact(text)
    title = re.match(r"国务院办公厅关于(\d{4})年部分节假日安排的通知", text_compact)
    require(title is not None, "Not the exact annual State Council General Office notice title")
    year = int(title[1])
    require(FIRST_YEAR <= year <= today.year + 1, "Notice year is outside the supported/published horizon")
    number = re.search(r"国办发明电〔(\d{4})〕(\d+)号", text_compact)
    require(number is not None, "Official document number is missing")
    require(re.search(r"经(?:党中央、)?国务院批准", text_compact) is not None, "State Council approval text is missing")
    signature = re.search(r"国务院办公厅(\d{4})年(\d{1,2})月(\d{1,2})日$", text_compact)
    require(signature is not None, "Official issuer/date signature is missing")
    published = date(*map(int, signature.groups()))
    require(published <= today, "Future-dated notices must not be imported")
    require(published.year == int(number[1]) and year - 1 <= published.year <= year, "Document date/year mismatch")
    require(not re.search(r"预测|预测版|预计安排|非正式", text_compact), "Prediction content is not accepted")

    clauses = list(re.finditer(r"[一二三四五六七八九十]+、([^：。]+)：", text_compact))
    require(1 <= len(clauses) <= 7, "Missing or unexpected holiday clauses")
    holidays, workdays, names_seen = [], [], set()
    for index, clause in enumerate(clauses):
        label = clause[1]
        names = re.split(r"、|和", label)
        require(len(names) == len(set(names)) and set(names) <= HOLIDAY_NAMES and not names_seen.intersection(names), "Unknown or repeated holiday name")
        names_seen.update(names)
        stop = clauses[index + 1].start() if index + 1 < len(clauses) else len(text_compact)
        content = re.split(r"鼓励|节假日期间|国务院办公厅", text_compact[clause.end():stop], maxsplit=1)[0]
        sentences = [s for s in content.split("。") if s]
        holiday_count = 0
        for sentence in sentences:
            if "放假" in sentence:
                holiday_count += 1
                date_part = sentence.split("放假", 1)[0]
                dates = parse_dates(date_part, year)
                require(1 <= len(dates) <= 2, "Unsupported holiday date expression")
                start, end = dates[0], dates[-1]
                require(len(dates) == 1 or "至" in date_part, "Ambiguous holiday interval")
                days = (end - start).days + 1
                require(1 <= days <= 15 and start.year == year and end.year == year, "Invalid annual holiday interval")
                count = re.search(r"(?:共|放假)(\d+)天", sentence)
                require(count is not None and int(count[1]) == days, "Holiday interval and official day count disagree")
                holidays.append({"name": label, "start": start.isoformat(), "end": end.isoformat()})
            elif "上班" in sentence:
                require(sentence.endswith("上班"), "Unsupported workday sentence")
                date_part = sentence[:-2]
                dates = parse_dates(date_part, year)
                require(dates and "至" not in date_part, "Unsupported workday expression")
                # All date-list characters must be accounted for; never silently drop a date.
                residue = DATE_TOKEN.sub("", date_part)
                require(not residue.strip("、，,和及"), "Unparsed workday text")
                for day in dates:
                    require(day.year == year and day.weekday() >= 5, "Make-up workday must be a weekend in the notice year")
                    workdays.append({"name": label, "date": day.isoformat()})
            else:
                raise InvalidData(f"Unrecognized notice sentence: {sentence}")
        require(holiday_count == 1, "Each clause must contain exactly one validated holiday interval")
    require(names_seen == HOLIDAY_NAMES, "Annual notice does not cover all seven holidays")
    occupied = set()
    for event in holidays:
        start, end = date.fromisoformat(event["start"]), date.fromisoformat(event["end"])
        days = {start + timedelta(days=n) for n in range((end - start).days + 1)}
        require(not occupied.intersection(days), "Holiday intervals overlap")
        occupied.update(days)
    work_dates = [date.fromisoformat(e["date"]) for e in workdays]
    require(len(work_dates) == len(set(work_dates)), "Repeated make-up workday")
    require(not occupied.intersection(work_dates), "A date cannot be both a holiday and a make-up workday")
    return {"year": year, "source": {"url": url, "title": title[0], "issuer": "国务院办公厅", "document_number": number[0], "published_on": published.isoformat(), "sha256": hashlib.sha256(text.encode()).hexdigest()}, "holidays": holidays, "workdays": workdays}


def discover(today=None, fetcher=fetch):
    today = today or datetime.now(timezone.utc).date()
    candidates = {}
    for page in range(10):
        params = {"q": "节假日安排的通知", "t": "zhengcelibrary", "searchfield": "title", "p": page, "n": 50, "sort": "pubtime", "sortType": 1}
        payload = json.loads(fetcher(SEARCH_ENDPOINT + "?" + urlencode(params)))
        require(payload.get("code") == 200, "Official search API returned an error")
        try:
            group = payload["searchVO"]["catMap"]["gongwen"]
            records, total = group["listVO"], int(group["totalCount"])
        except (KeyError, TypeError, ValueError) as exc:
            raise InvalidData("Official search response changed; no calendar files will be replaced") from exc
        require(isinstance(records, list) and total > 0, "Official search returned no historical notices; refusing an unreliable empty result")
        for record in records:
            title = compact(re.sub(r"<[^>]+>", "", record.get("title", "")))
            match = re.fullmatch(r"国务院办公厅关于(\d{4})年部分节假日安排的通知", title)
            if not match or record.get("puborg") != "国务院办公厅":
                continue
            year = int(match[1])
            if FIRST_YEAR <= year <= today.year + 1:
                url = official_url(record["url"])
                if year in candidates:
                    require(candidates[year] == url, f"Multiple official originals found for {year}; manual review required")
                candidates[year] = url
        if (page + 1) * 50 >= total:
            require(bool(candidates), "No supported official annual notices found; discovery needs maintenance")
            return [candidates[year] for year in sorted(candidates)]
    raise InvalidData("Official search pagination exceeded safe limit")


def event_key(year, kind, event):
    return f"cn-holiday/{year}/{kind}/{event['name'] if kind == 'holiday' else event['date']}"


def add_identity(dataset, stamp=None, legacy=None):
    stamp = stamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dataset = copy.deepcopy(dataset)
    dataset["created_at"] = stamp
    for kind, field in (("holiday", "holidays"), ("workday", "workdays")):
        for event in dataset[field]:
            event["uid"] = (legacy or {}).get((kind, event.get("start", event.get("date")))) or str(uuid.uuid5(uuid.NAMESPACE_URL, event_key(dataset["year"], kind, event))) + "@china-holiday-calendar"
    return dataset


def without_identity(dataset):
    result = copy.deepcopy(dataset)
    result.pop("created_at", None)
    for field in ("holidays", "workdays"):
        for event in result[field]:
            event.pop("uid", None)
    return result


def load_years(root):
    datasets = []
    for path in sorted((root / "data/years").glob("*.json")):
        dataset = json.loads(path.read_text(encoding="utf-8"))
        require(path.stem == str(dataset["year"]), "Year file name mismatch")
        source_text = (root / f"data/sources/{dataset['year']}.txt").read_text(encoding="utf-8")
        parsed = parse_notice(source_text, dataset["source"]["url"])
        require(without_identity(dataset) == parsed, f"Saved year {dataset['year']} disagrees with archived official evidence")
        datetime.strptime(dataset["created_at"], "%Y%m%dT%H%M%SZ")
        datasets.append(dataset)
    require(bool(datasets), "No validated annual datasets exist")
    return datasets


def escape_text(value):
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace(";", "\\;").replace(",", "\\,")


def fold_line(line):
    chunks, chunk = [], ""
    for char in line:
        if len((chunk + char).encode("utf-8")) > 75:
            chunks.append(chunk)
            chunk = " "
        chunk += char
    chunks.append(chunk)
    return "\r\n".join(chunks)


def render_calendar(datasets):
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//China Holiday Calendar//Official Mainland China Holidays//ZH", "CALSCALE:GREGORIAN", "METHOD:PUBLISH", "X-WR-CALNAME:中国大陆节假日及调休", "X-WR-TIMEZONE:Asia/Shanghai", "REFRESH-INTERVAL;VALUE=DURATION:P7D", "X-PUBLISHED-TTL:P7D"]
    all_events = []
    for dataset in datasets:
        for kind, field in (("holiday", "holidays"), ("workday", "workdays")):
            for event in dataset[field]:
                start = date.fromisoformat(event.get("start", event.get("date")))
                end = date.fromisoformat(event.get("end", event.get("date"))) + timedelta(days=1)
                summary = event["name"] + "（放假）" if kind == "holiday" else "补班：" + event["name"] + "补班"
                description = f"{dataset['source']['title']}；{dataset['source']['document_number']}。" + ("调休上班日。" if kind == "workday" else "此区间包含正式通知安排的连续假期。")
                all_events.append((start, event["uid"], ["BEGIN:VEVENT", "UID:" + event["uid"], "DTSTAMP:" + dataset["created_at"], "DTSTART;VALUE=DATE:" + start.strftime("%Y%m%d"), "DTEND;VALUE=DATE:" + end.strftime("%Y%m%d"), "SUMMARY:" + escape_text(summary), "DESCRIPTION:" + escape_text(description), "URL:" + dataset["source"]["url"], "CATEGORIES:" + ("放假" if kind == "holiday" else "调休补班"), "STATUS:CONFIRMED", "SEQUENCE:0", "TRANSP:TRANSPARENT", "END:VEVENT"]))
    for _, _, event_lines in sorted(all_events):
        lines.extend(event_lines)
    lines.append("END:VCALENDAR")
    output = ("\r\n".join(fold_line(line) for line in lines) + "\r\n").encode("utf-8")
    validate_ics(output, datasets)
    return output


def validate_ics(raw, datasets=None):
    require(raw.startswith(b"BEGIN:VCALENDAR\r\n") and raw.endswith(b"END:VCALENDAR\r\n"), "Invalid VCALENDAR boundaries")
    require(b"\n" not in raw.replace(b"\r\n", b"") and b"\r" not in raw.replace(b"\r\n", b""), "ICS requires CRLF line endings")
    require(all(len(line) <= 75 for line in raw.split(b"\r\n")), "ICS line is longer than 75 octets")
    text = re.sub(r"\r\n[ \t]", "", raw.decode("utf-8"))
    stack, events, current, top = [], [], None, {}
    for line in text.split("\r\n"):
        if not line:
            continue
        key, separator, value = line.partition(":")
        require(separator and re.fullmatch(r"[A-Z0-9-]+(?:;[A-Z0-9-]+=[A-Z0-9/+-]+)*", key), "Malformed ICS property")
        if key == "BEGIN":
            require((value == "VCALENDAR" and not stack) or (value == "VEVENT" and stack == ["VCALENDAR"]), "Unexpected ICS component")
            stack.append(value)
            if value == "VEVENT":
                current = {}
        elif key == "END":
            require(stack and stack.pop() == value, "Unbalanced ICS component")
            if value == "VEVENT":
                events.append(current)
                current = None
        else:
            target = current if current is not None else top
            require(key not in target, "Repeated ICS singleton property")
            target[key] = value
    require(not stack and top.get("VERSION") == "2.0" and top.get("PRODID"), "Incomplete ICS calendar header")
    uids = set()
    for event in events:
        require({"UID", "DTSTAMP", "DTSTART;VALUE=DATE", "DTEND;VALUE=DATE", "SUMMARY"} <= event.keys(), "Missing required event properties")
        require(event["UID"] and event["UID"] not in uids and not re.search(r"\s", event["UID"]), "Duplicate or invalid event UID")
        uids.add(event["UID"])
        datetime.strptime(event["DTSTAMP"], "%Y%m%dT%H%M%SZ")
        start = datetime.strptime(event["DTSTART;VALUE=DATE"], "%Y%m%d").date()
        end = datetime.strptime(event["DTEND;VALUE=DATE"], "%Y%m%d").date()
        require(start < end, "DTEND must be exclusive and later than DTSTART")
    require(bool(events), "Calendar must not be empty")
    if datasets is not None:
        expected = {}
        for dataset in datasets:
            for field in ("holidays", "workdays"):
                for event in dataset[field]:
                    uid = event["uid"]
                    require(uid not in expected, "UID collision in annual history")
                    expected[uid] = (date.fromisoformat(event.get("start", event.get("date"))), date.fromisoformat(event.get("end", event.get("date"))) + timedelta(days=1))
        require(uids == set(expected), "ICS history is incomplete or contains unknown events")
        for event in events:
            actual = tuple(datetime.strptime(event[key], "%Y%m%d").date() for key in ("DTSTART;VALUE=DATE", "DTEND;VALUE=DATE"))
            require(actual == expected[event["UID"]], "ICS dates disagree with validated annual data")
    return events


def atomic_write(path, contents):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as target:
        temporary = Path(target.name)
        try:
            target.write(contents)
            target.flush()
            os.fsync(target.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def record_check(root, years, added):
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    status = {"checked_at": stamp, "result": "updated" if added else "no_new_official_notice", "available_years": sorted(years), "added_years": added}
    atomic_write(root / "data/check-status.json", (json.dumps(status, ensure_ascii=False, indent=2) + "\n").encode())
    path = root / "CHECKS.md"
    previous = path.read_text(encoding="utf-8") if path.exists() else "# 官方公告检查记录\n\n仅记录成功完成的官方查询；抓取或校验失败见 Actions 运行日志。\n"
    result = "新增正式年度 " + ", ".join(map(str, added)) if added else "未发现新的正式年度公告；日历保持不变"
    atomic_write(path, (previous.rstrip() + f"\n\n- {stamp}：{result}。\n").encode())


def update(root, urls=None, fetcher=fetch):
    existing = load_years(root)
    current = (root / "holiday.ics").read_bytes()
    validate_ics(current, existing)
    years = {data["year"]: data for data in existing}
    staged = {}
    # Fetch and validate everything before writing any repository file.
    for url in urls if urls is not None else discover(fetcher=fetcher):
        official_url(url)
        text = article_text(fetcher(url))
        parsed = parse_notice(text, url)
        year = parsed["year"]
        if year in years:
            comparable = without_identity(years[year])
            # The same original can be served through /content/ and /zhengceku/.
            for candidate in (comparable, parsed):
                candidate["source"].pop("url", None)
                candidate["source"].pop("sha256", None)
            require(comparable == parsed, f"Existing year {year} changed: review the official revision before editing historical events")
            continue
        dataset = add_identity(parse_notice(text, url))
        years[year] = dataset
        staged[root / f"data/years/{year}.json"] = (json.dumps(dataset, ensure_ascii=False, indent=2) + "\n").encode()
        staged[root / f"data/sources/{year}.txt"] = text.encode()
    if not staged:
        record_check(root, years, [])
        print("No new formally announced year. Existing calendar preserved byte for byte.")
        return False
    datasets = [years[year] for year in sorted(years)]
    new_calendar = render_calendar(datasets)
    previous_uids = {e["UID"] for e in validate_ics(current)}
    require(previous_uids <= {e["UID"] for e in validate_ics(new_calendar)}, "An update would remove historical UIDs")
    new_years = sorted(set(years) - {item["year"] for item in existing})
    log = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    entry = f"\n## {stamp}\n\n" + "\n".join(f"- 新增 {year} 年正式安排：[{years[year]['source']['document_number']}]({years[year]['source']['url']})；原始公告 SHA-256：`{years[year]['source']['sha256']}`。" for year in new_years) + "\n"
    staged[root / "CHANGELOG.md"] = (log.rstrip() + "\n" + entry).encode()
    # Calendar is installed last, after all parsing, evidence and UID checks pass.
    for path, data in staged.items():
        atomic_write(path, data)
    atomic_write(root / "holiday.ics", new_calendar)
    record_check(root, years, new_years)
    print("Added official years: " + ", ".join(map(str, new_years)))
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["update", "validate"])
    parser.add_argument("--source-url", help="Optional exact www.gov.cn original-notice URL; never a third-party calendar")
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    try:
        if args.command == "update":
            update(args.root, [args.source_url] if args.source_url else None)
        else:
            datasets = load_years(args.root)
            raw = (args.root / "holiday.ics").read_bytes()
            events = validate_ics(raw, datasets)
            require(raw == render_calendar(datasets), "Calendar is not the deterministic rendering of the validated history")
            print(f"PASS: years={[d['year'] for d in datasets]}, events={len(events)}, unique_uids={len(events)}")
    except (InvalidData, OSError, ValueError, KeyError, TypeError) as exc:
        print(f"FAILED: {exc}. Existing published calendar must not be overwritten.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
