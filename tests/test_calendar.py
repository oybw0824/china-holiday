import copy
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
from icalendar import Calendar

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import holiday_calendar as h
SOURCE = 'https://www.gov.cn/zhengce/content/202611/content_7085101.htm'
NOTICE = (ROOT / 'data/sources/2027.txt').read_text(encoding='utf-8')


def as_html(text):
    import html
    return ('<html><div id="UCAP-CONTENT">' + ''.join('<p>' + html.escape(line) + '</p>' for line in text.splitlines()) + '</div></html>').encode()


def snapshot(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}


class CalendarTests(unittest.TestCase):
    def test_2027_with_independent_rfc_parser(self):
        raw = (ROOT / 'holiday.ics').read_bytes()
        events = Calendar.from_ical(raw).walk('VEVENT')
        current_year = [e for e in events if e.decoded('DTSTART').year == 2027]
        self.assertEqual(12, len(current_year))
        holidays, workdays = set(), set()
        for e in events:
            self.assertFalse(e.errors)
        for e in current_year:
            day, end = e.decoded('DTSTART'), e.decoded('DTEND')
            target = workdays if str(e['SUMMARY']) == '班' else holidays
            while day < end:
                target.add(day.isoformat())
                day += timedelta(days=1)
        self.assertEqual(29, len(holidays))
        self.assertEqual({'2027-01-31','2027-02-14','2027-05-08','2027-09-26','2027-10-09'}, workdays)
        self.assertEqual(len(events), len({str(e['UID']) for e in events}))
        h.validate_ics(raw, h.load_years(ROOT))

    def test_original_uids_are_preserved(self):
        actual = {e['UID'] for e in h.validate_ics((ROOT / 'holiday.ics').read_bytes())}
        original = {'d227fba1-d0d2-5817-9e49-3bcc4920a023','2666e864-f601-587d-98fd-8e356f0ef4cb','a627dfa8-1e38-5e81-a7f9-cf6d1686cd50','cfa350e4-6015-5717-a646-ed4670f390f3','c858eade-7498-5282-9029-e54404897b9a','54fa1d0e-41a7-59df-a234-64736c537a5e','e8a77f12-0969-5d1b-bfef-92741aa06873','bc4aa134-b0f9-52e1-add5-ed9c3ec9b723','91a1c0e2-aef3-57a0-8944-92abef5e2146','128b4c40-2287-5c00-80f4-bf125f273e02','5b02c04c-e3a4-5b27-8d02-2c6dfaf7a5ae','03abf216-0673-5691-9677-a8edd3b892b0'}
        self.assertTrue({uid + '@openai' for uid in original} <= actual)

    def test_deterministic_rendering_and_archived_source(self):
        datasets = h.load_years(ROOT)
        data = next(d for d in datasets if d['year'] == 2027)
        self.assertEqual(h.parse_notice(NOTICE, SOURCE), h.without_identity(data))
        self.assertEqual((ROOT / 'holiday.ics').read_bytes(), h.render_calendar(datasets))

    def test_utf8_folding_and_text_escaping(self):
        line = 'DESCRIPTION:' + h.escape_text('中文;逗号,斜线\\换行\n' * 12)
        folded = h.fold_line(line)
        self.assertTrue(all(len(part.encode()) <= 75 for part in folded.split('\r\n')))
        self.assertEqual(line, folded.replace('\r\n ', ''))
        self.assertIn('\\;', line)
        self.assertIn('\\,', line)
        self.assertIn('\\n', line)

    def test_split_feeds_are_complete_disjoint_and_preserve_event_identity(self):
        parsed = {name: Calendar.from_ical((ROOT / name).read_bytes()) for name in h.FEEDS}
        events = {name: {str(e['UID']): e for e in cal.walk('VEVENT')} for name, cal in parsed.items()}
        all_ids = set(events['holiday.ics'])
        holidays, workdays = set(events['holidays.ics']), set(events['workdays.ics'])
        self.assertFalse(holidays & workdays)
        self.assertEqual(all_ids, holidays | workdays)
        for name in ('holidays.ics', 'workdays.ics'):
            for uid, event in events[name].items():
                self.assertFalse(event.errors)
                self.assertEqual(events['holiday.ics'][uid].to_ical(), event.to_ical())
        self.assertEqual('中国大陆放假日历', str(parsed['holidays.ics']['X-WR-CALNAME']))
        self.assertEqual('中国大陆调休补班', str(parsed['workdays.ics']['X-WR-CALNAME']))
        self.assertTrue(all(str(e['SUMMARY']).endswith('（放假）') for e in events['holidays.ics'].values()))
        self.assertTrue(all(str(e['SUMMARY']) == '班' for e in events['workdays.ics'].values()))
        self.assertEqual(7, sum(e.decoded('DTSTART').year == 2027 for e in events['holidays.ics'].values()))
        self.assertEqual(5, sum(e.decoded('DTSTART').year == 2027 for e in events['workdays.ics'].values()))

    def test_migration_and_repeated_generation_preserve_merged_calendar(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'repo'
            shutil.copytree(ROOT, root, ignore=shutil.ignore_patterns('__pycache__', '.git'))
            before = (root / 'holiday.ics').read_bytes()
            (root / 'holidays.ics').unlink()
            (root / 'workdays.ics').unlink()
            data = h.load_years(root)
            h.validate_calendars(root, data, require_all=False)
            self.assertTrue(h.write_calendars(root, h.render_feeds(data)))
            self.assertEqual(before, (root / 'holiday.ics').read_bytes())
            self.assertEqual({'holiday.ics': 12, 'holidays.ics': 7, 'workdays.ics': 5}, h.validate_calendars(root, data))
            self.assertFalse(h.write_calendars(root, h.render_feeds(data)))

    def test_invalid_split_feed_blocks_update_without_overwriting_other_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'repo'
            shutil.copytree(ROOT, root, ignore=shutil.ignore_patterns('__pycache__', '.git'))
            (root / 'workdays.ics').write_bytes((root / 'holidays.ics').read_bytes())
            before = snapshot(root)
            with self.assertRaises(h.InvalidData):
                h.update(root, [SOURCE], fetcher=lambda _: as_html(NOTICE))
            self.assertEqual(before, snapshot(root))

    def test_uid_determinism(self):
        parsed = h.parse_notice(NOTICE, SOURCE)
        a, b = h.add_identity(parsed), h.add_identity(parsed)
        self.assertEqual([e['uid'] for e in a['holidays']], [e['uid'] for e in b['holidays']])

    def test_short_workday_titles_and_revision_metadata(self):
        datasets = h.load_years(ROOT)
        expected = {event['uid']: (event.get('sequence', 0), event.get('updated_at', dataset['created_at'])) for dataset in datasets for event in dataset['workdays']}
        events = Calendar.from_ical(h.render_calendar(datasets, 'workday')).walk('VEVENT')
        for event in events:
            self.assertEqual('班', str(event['SUMMARY']))
            sequence, stamp = expected[str(event['UID'])]
            self.assertEqual(sequence, int(event['SEQUENCE']))
            self.assertEqual(stamp, event['DTSTAMP'].to_ical().decode())
        fresh = h.add_identity(h.parse_notice(NOTICE, SOURCE))
        for event in Calendar.from_ical(h.render_calendar([fresh], 'workday')).walk('VEVENT'):
            self.assertEqual('班', str(event['SUMMARY']))
            self.assertEqual(0, int(event['SEQUENCE']))

    def test_invalid_notice_data_rejected(self):
        for text in (NOTICE.replace('共9天','共8天'), NOTICE.replace('1月1日（周五）','1月1日（周四）'), re.sub(r'六、中秋节：[^\n]*\n','',NOTICE), NOTICE.replace('国办发明电〔2026〕10号',''), NOTICE.replace('国务院办公厅关于2027年','网友预测2027年'), NOTICE.replace('2026年11月5日','2029年11月5日'), NOTICE.replace('1月31日（周日）、2月14日（周日）上班','1月31日（周日）至2月14日（周日）上班')):
            with self.subTest(text=text[:40]), self.assertRaises(h.InvalidData):
                h.parse_notice(text,SOURCE)

    def test_only_exact_official_https_articles_accepted(self):
        for url in (SOURCE.replace('https:','http:'), SOURCE.replace('www.gov.cn','www.gov.cn.evil.example'), 'https://people.com.cn/notice.html', 'https://www.gov.cn/news/guess.htm', SOURCE+'?override=1'):
            with self.subTest(url=url), self.assertRaises(h.InvalidData):
                h.official_url(url)

    def test_duplicate_uid_rejected(self):
        data = copy.deepcopy(h.load_years(ROOT))
        data[0]['holidays'][1]['uid'] = data[0]['holidays'][0]['uid']
        with self.assertRaises(h.InvalidData): h.render_calendar(data)

    def test_official_html_body_required(self):
        self.assertEqual(NOTICE,h.article_text(as_html(NOTICE)))
        with self.assertRaises(h.InvalidData): h.article_text(b'<html>missing body</html>')

    def test_changed_search_response_fails_closed(self):
        for response in ({'code':500},{'code':200},{'code':200,'searchVO':{'catMap':{'gongwen':{'listVO':[],'totalCount':0}}}}):
            with self.subTest(response=response), self.assertRaises(h.InvalidData):
                h.discover(fetcher=lambda _:json.dumps(response).encode())

    def test_discovery_does_not_invent_unannounced_years(self):
        payload = {'code':200,'searchVO':{'catMap':{'gongwen':{'listVO':[{'title':'国务院办公厅关于2027年部分<em>节假日安排</em>的通知','puborg':'国务院办公厅','url':SOURCE},{'title':'2028年放假预测','puborg':'国务院办公厅','url':SOURCE}],'totalCount':2}}}}
        self.assertEqual([SOURCE],h.discover(fetcher=lambda _:json.dumps(payload).encode()))

    def test_failed_update_preserves_every_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'repo'
            shutil.copytree(ROOT,root,ignore=shutil.ignore_patterns('__pycache__','.git'))
            before=snapshot(root)
            with self.assertRaises(h.InvalidData):
                h.update(root,[SOURCE],fetcher=lambda _:as_html(NOTICE.replace('共9天','共8天')))
            self.assertEqual(before,snapshot(root))

    def test_network_failure_preserves_every_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'repo'
            shutil.copytree(ROOT,root,ignore=shutil.ignore_patterns('__pycache__','.git'))
            before=snapshot(root)
            def fail(_): raise OSError('test network outage')
            with self.assertRaises(OSError): h.update(root,[SOURCE],fetcher=fail)
            self.assertEqual(before,snapshot(root))

    def test_no_new_notice_keeps_calendar_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'repo'
            shutil.copytree(ROOT,root,ignore=shutil.ignore_patterns('__pycache__','.git'))
            before=(root/'holiday.ics').read_bytes()
            self.assertFalse(h.update(root,[SOURCE],fetcher=lambda _:as_html(NOTICE)))
            self.assertEqual(before,(root/'holiday.ics').read_bytes())

    def test_real_new_year_import_preserves_history_and_is_idempotent(self):
        # Exercise adding an already announced year; no future calendar is fabricated.
        with tempfile.TemporaryDirectory() as directory, patch.object(h,'FIRST_YEAR',2026):
            root=Path(directory)
            text=(ROOT/'tests/fixtures/2026.txt').read_text()
            old=h.add_identity(h.parse_notice(text,'https://www.gov.cn/zhengce/zhengceku/202511/content_7047091.htm'))
            (root/'data/years').mkdir(parents=True)
            (root/'data/sources').mkdir()
            (root/'data/years/2026.json').write_text(json.dumps(old,ensure_ascii=False))
            (root/'data/sources/2026.txt').write_text(text)
            (root/'CHANGELOG.md').write_text('# Test changelog\n')
            (root/'holiday.ics').write_bytes(h.render_calendar([old]))
            old_uids={e['UID'] for e in h.validate_ics((root/'holiday.ics').read_bytes())}
            self.assertTrue(h.update(root,[SOURCE],fetcher=lambda _:as_html(NOTICE)))
            result=(root/'holiday.ics').read_bytes()
            self.assertTrue(old_uids <= {e['UID'] for e in h.validate_ics(result)})
            self.assertEqual([2026,2027],[item['year'] for item in h.load_years(root)])
            split_ids = set()
            for name, (kind, _) in h.FEEDS.items():
                if kind != 'all':
                    selected = {e['UID'] for e in h.validate_ics((root / name).read_bytes(), h.load_years(root), kind)}
                    self.assertFalse(split_ids & selected)
                    split_ids |= selected
            self.assertEqual({e['UID'] for e in h.validate_ics(result)}, split_ids)
            self.assertFalse(h.update(root,[SOURCE],fetcher=lambda _:as_html(NOTICE)))
            self.assertEqual(result,(root/'holiday.ics').read_bytes())

    def test_real_combined_holiday_notice(self):
        with patch.object(h,'FIRST_YEAR',2025):
            text=(ROOT/'tests/fixtures/2025.txt').read_text()
            parsed=h.parse_notice(text,'https://www.gov.cn/zhengce/zhengceku/202411/content_6986383.htm')
            combined=[e for e in parsed['holidays'] if '中秋节' in e['name'] and '国庆节' in e['name']]
            self.assertEqual(1,len(combined))
            self.assertEqual('2025-10-08',combined[0]['end'])


if __name__ == '__main__': unittest.main()
