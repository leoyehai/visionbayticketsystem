#!/usr/bin/env python3
"""
Daily Jira -> dashboard sync.
Fetches VOTS issues (type=事件) created/updated in the last 2 days that are
NOT already present in index.html's INCIDENTS array, and appends them with
best-effort field mapping (same heuristics used in manual updates).

New entries are added with placeholder scope/action/lesson text flagged for
human review -- this script cannot replicate the nuanced Traditional Chinese
summarization, root-cause narrative, or repeat-node cross-referencing that a
human/Claude session does. Search index.html for 'NEEDS REVIEW' after each run.
"""
import os, re, sys, json
from datetime import datetime
import urllib.request

JIRA_BASE = os.environ['JIRA_BASE_URL'].rstrip('/')
JIRA_EMAIL = os.environ['JIRA_EMAIL']
JIRA_TOKEN = os.environ['JIRA_API_TOKEN']
INDEX_PATH = os.environ.get('INDEX_PATH', 'index.html')

CAT_MAP = {'硬體': 'hw', '網路': 'net', '設施': 'cool', '維運': 'ops'}
TOOL_MAP = {'hw': 'dcgm', 'net': 'netris', 'cool': 'intouch', 'ops': 'bms'}
EVENT_TYPE_ID = '10113'  # "事件" issue type (confirmed from Jira schema)

def jira_get(path, params=None):
    import base64
    url = f'{JIRA_BASE}{path}'
    if params:
        from urllib.parse import urlencode
        url += '?' + urlencode(params)
    req = urllib.request.Request(url)
    token = base64.b64encode(f'{JIRA_EMAIL}:{JIRA_TOKEN}'.encode()).decode()
    req.add_header('Authorization', f'Basic {token}')
    req.add_header('Accept', 'application/json')
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())

def fetch_recent_issues():
    jql = 'project = VOTS AND updated >= -2d ORDER BY created ASC'
    fields = 'summary,status,created,resolutiondate,customfield_10159,customfield_10161,priority,issuetype'
    data = jira_get('/rest/api/3/search', {'jql': jql, 'fields': fields, 'maxResults': 100})
    return [i for i in data['issues'] if i['fields']['issuetype']['id'] == EVENT_TYPE_ID]

def parse_down_minutes(cf10159, created, resolutiondate):
    if cf10159:
        m = re.search(r'(\d+)\s*(分鐘|mins?)', str(cf10159))
        if m:
            return int(m.group(1))
    if resolutiondate:
        try:
            t1 = datetime.fromisoformat(created.replace('Z', '+00:00'))
            t2 = datetime.fromisoformat(resolutiondate.replace('Z', '+00:00'))
            return max(0, int((t2 - t1).total_seconds() / 60))
        except Exception:
            pass
    return 0

def classify_p(down, priority_name, status_category):
    if priority_name in ('High', 'Highest'):
        return 'P1'
    if down >= 500:
        return 'P1'
    if down >= 100:
        return 'P2'
    if down >= 1:
        return 'P3'
    if status_category != 'done':
        return 'P2'
    return 'P4'

def map_status(status_category):
    return {'done': 'fixed', 'indeterminate': 'wip', 'new': 'open'}.get(status_category, 'open')

def esc(s):
    return (s or '').replace('\\', '\\\\').replace("'", "\\'").replace('\n', ' ').strip()

def build_entry(issue):
    key = issue['key']
    f = issue['fields']
    summary = esc(f['summary'])
    cat = CAT_MAP.get(f.get('customfield_10161'), 'ops')
    created = f['created']
    date_str = datetime.fromisoformat(created.replace('Z', '+00:00')).strftime('%m/%d')
    down = parse_down_minutes(f.get('customfield_10159'), created, f.get('resolutiondate'))
    priority_name = f.get('priority', {}).get('name', 'Medium')
    prio = '高' if priority_name in ('High', 'Highest') else '中'
    status_cat = f['status']['statusCategory']['key']
    status = map_status(status_cat)
    p = classify_p(down, priority_name, status_cat)
    tool = TOOL_MAP.get(cat, 'bms')
    owner = 'tier3' if priority_name in ('High', 'Highest') else 'tier2'
    return (f"  {{date:'{date_str}',cat:'{cat}',title:'{summary}',down:'{down}',prio:'{prio}',"
            f"p:'{p}',status:'{status}',scope:'⚠️ NEEDS REVIEW',"
            f"action:'⚠️ NEEDS REVIEW — 自動同步產生，請人工補充處理細節',"
            f"lesson:'⚠️ NEEDS REVIEW — 自動同步產生，請人工補充根因與教訓',"
            f"tool:'{tool}',owner:'{owner}',jira:'{key}'}}")

def main():
    issues = fetch_recent_issues()
    with open(INDEX_PATH, encoding='utf-8') as fp:
        content = fp.read()

    m = re.search(r"(const INCIDENTS = \[)(.*?)(\n\];)", content, re.S)
    if not m:
        print('ERROR: could not locate INCIDENTS array in index.html', file=sys.stderr)
        sys.exit(1)
    body = m.group(2)
    existing_keys = set(re.findall(r"jira:'(VOTS-\d+)'", body))

    new_entries = []
    added_keys = []
    for issue in issues:
        key = issue['key']
        if key in existing_keys:
            continue
        new_entries.append(build_entry(issue))
        added_keys.append(key)

    if not new_entries:
        print('No new issues to add.')
        return

    new_body = body.rstrip()
    if new_body.endswith(','):
        new_body = new_body[:-1]
    new_body += ',\n' + ',\n'.join(new_entries)

    new_content = content[:m.start(2)] + new_body + content[m.end(2):]
    with open(INDEX_PATH, 'w', encoding='utf-8') as fp:
        fp.write(new_content)
    print(f'Added {len(new_entries)} new issue(s): {added_keys}')

if __name__ == '__main__':
    main()
