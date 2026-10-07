"""Durable public Search observations; no account credentials or outbound messages.

One page and its cursor commit together. Vercel Cron resumes leased work after
browser closure or process restart. Cached pages still pass the normal eligibility
and ranking pipeline before a customer can select a supplier.
"""
from contextlib import contextmanager
from dataclasses import replace
import hashlib
import json
import os
import time
from uuid import uuid4

from farq.live_haraj import HarajLiveClient, LiveUnavailable, ad_from_item

SCHEMA = """
create table if not exists haraj_public_control (
 id integer primary key, state text not null, retry_at double precision not null default 0
);
insert into haraj_public_control(id,state) values(1,'READY') on conflict(id) do nothing;
create table if not exists haraj_public_jobs (
 id text primary key, query text not null, city text, page_size integer not null,
 next_page integer not null default 1, state text not null default 'FETCHING',
 next_run double precision not null default 0, lease_until double precision not null default 0,
 lease_token text, last_success double precision, last_error_code text,
 updated_at double precision not null
);
create index if not exists haraj_public_jobs_due on haraj_public_jobs(next_run, lease_until);
create table if not exists haraj_public_pages (
 job_id text not null references haraj_public_jobs(id), page integer not null,
 payload text not null, observed_at double precision not null,
 primary key(job_id, page)
);
create table if not exists haraj_public_ads (
 id text primary key, payload text not null, observed_at double precision not null,
 first_observed_at double precision not null, source text not null default 'haraj-public-search'
);
"""
# Only fields the existing evidenced Search operation requests; no headers, tokens,
# cookies, login requests, profiles or private conversations can reach these tables.
FIELDS = ('id', 'title', 'postDate', 'authorUsername', 'authorId', 'URL', 'bodyTEXT',
          'city', 'geoNeighborhood', 'tags', 'thumbURL', 'hasImage', 'status', 'price')


class HarvestStore:
    def __init__(self, store, clock=time.time):
        self.store, self.clock = store, clock
        self.pg = hasattr(store, '_pool')
        if not self.pg:
            with store._db_lock:
                store._connection.executescript(SCHEMA)
        else:
            with self.transaction() as execute:
                execute("insert into haraj_public_control(id,state) values(1,'READY') on conflict(id) do nothing")

    @contextmanager
    def transaction(self):
        if self.pg:
            with self.store._pool.connection() as conn:
                with conn.transaction():
                    yield lambda sql, args=(): conn.execute(sql.replace('?', '%s'), args)
        else:
            with self.store._db_lock, self.store._connection:
                yield self.store._connection.execute

    def ensure(self, query, city, size):
        key = hashlib.sha256(json.dumps([query, city, size], ensure_ascii=False).encode()).hexdigest()
        with self.transaction() as execute:
            execute('insert into haraj_public_jobs(id,query,city,page_size,updated_at) values(?,?,?,?,?) on conflict(id) do nothing',
                    (key, query, city, size, self.clock()))
        return key

    def cached(self, key, page, ttl):
        with self.transaction() as execute:
            row = execute('select payload,observed_at from haraj_public_pages where job_id=? and page=?', (key, page)).fetchone()
        return json.loads(row['payload']) if row and row['observed_at'] >= self.clock() - ttl else None

    def paused(self):
        with self.transaction() as execute:
            row = execute('select state,retry_at from haraj_public_control where id=1').fetchone()
        return row['state'] == 'CHALLENGE' or row['retry_at'] > self.clock()

    def pause(self, code):
        if code not in ('challenge', 'rate_limited'):
            return
        with self.transaction() as execute:
            execute("update haraj_public_control set state=?,retry_at=? where id=1 and state <> 'CHALLENGE'",
                    ('CHALLENGE' if code == 'challenge' else 'RATE_LIMITED', self.clock() + 3600))

    def status(self, key):
        with self.transaction() as execute:
            row = execute('select state,next_page,last_success,last_error_code from haraj_public_jobs where id=?', (key,)).fetchone()
            control = execute('select state,retry_at from haraj_public_control where id=1').fetchone()
        result = dict(row) if row else None
        if result and (control['state'] == 'CHALLENGE' or control['retry_at'] > self.clock()):
            result['state'] = control['state']
        return result

    def save(self, key, page, data, refresh_seconds=21600, lease=None):
        now = self.clock()
        if not isinstance(data.get('items'), list) or not isinstance((data.get('pageInfo') or {}).get('hasNextPage'), bool):
            raise LiveUnavailable('invalid_search_response')
        items = [{k: item[k] for k in FIELDS if k in item} for item in data.get('items', []) if item.get('id')]
        for item in items:
            if isinstance(item.get('price'), dict):
                item['price'] = {k: item['price'][k] for k in ('formattedPrice', 'inputPrice') if k in item['price']}
        # Validate the whole observation before touching persistent serving data.
        ads = [ad_from_item(item) for item in items]
        has_next = bool((data.get('pageInfo') or {}).get('hasNextPage')) and bool(items)
        payload = json.dumps({'items': items, 'pageInfo': {'hasNextPage': has_next}}, ensure_ascii=False)
        with self.transaction() as execute:
            row = execute('select next_page,lease_token from haraj_public_jobs where id=?', (key,)).fetchone()
            if not row or (lease is not None and row['lease_token'] != lease):
                return False
            execute('insert into haraj_public_pages(job_id,page,payload,observed_at) values(?,?,?,?) on conflict(job_id,page) do update set payload=excluded.payload,observed_at=excluded.observed_at', (key, page, payload, now))
            for ad in ads:
                execute('insert into haraj_public_ads(id,payload,observed_at,first_observed_at) values(?,?,?,?) on conflict(id) do update set payload=excluded.payload,observed_at=excluded.observed_at', (ad.id, ad.model_dump_json(), now, now))
            # Advance only the contiguous committed cursor, never skip a failed page.
            cursor = row['next_page']
            terminal = False
            while True:
                cached = execute('select payload,observed_at from haraj_public_pages where job_id=? and page=?', (key, cursor)).fetchone()
                if not cached or cached['observed_at'] < now - refresh_seconds:
                    break
                terminal = not json.loads(cached['payload'])['pageInfo']['hasNextPage']
                cursor += 1
                if terminal:
                    break
            state = 'COMPLETE' if terminal else 'FETCHING'
            execute('update haraj_public_jobs set next_page=?,state=?,next_run=?,last_success=?,last_error_code=null,updated_at=? where id=?',
                    (1 if terminal else cursor, state, now + refresh_seconds if terminal else now + 5, now, now, key))
            if lease is not None:
                execute('update haraj_public_jobs set lease_until=0,lease_token=null where id=? and lease_token=?', (key, lease))
        return True

    def claim(self):
        if self.paused():
            return None
        now, token = self.clock(), uuid4().hex
        with self.transaction() as execute:
            suffix = ' for update skip locked' if self.pg else ''
            row = execute("select * from haraj_public_jobs where state <> 'CHALLENGE' and next_run<=? and lease_until<=? order by next_run,id limit 1" + suffix, (now, now)).fetchone()
            if not row:
                return None
            execute("update haraj_public_jobs set lease_until=?,lease_token=?,state='FETCHING',updated_at=? where id=?", (now + 90, token, now, row['id']))
        return {**dict(row), 'lease_token': token}

    def fail(self, key, lease, code):
        self.pause(code)
        now = self.clock()
        state = 'CHALLENGE' if code == 'challenge' else 'RATE_LIMITED' if code == 'rate_limited' else 'RETRY'
        delay = 3600 if code == 'rate_limited' else 300
        with self.transaction() as execute:
            execute('update haraj_public_jobs set state=?,last_error_code=?,next_run=?,lease_until=0,lease_token=null,updated_at=? where id=? and lease_token=?',
                    (state, code, now + delay, now, key, lease))


def error_code(exc):
    text = str(exc).lower()
    if '429' in text or 'rate limit' in text:
        return 'rate_limited'
    if '403' in text or 'challenge' in text or 'captcha' in text:
        return 'challenge'
    return 'source_unavailable'


class CachedHarajClient(HarajLiveClient):
    def __init__(self, config, harvest):
        super().__init__(config)
        self.harvest = harvest
        self.jobs = set()

    def _post(self, variables):
        key = self.harvest.ensure(variables['search'], variables.get('city'), variables['limit'])
        self.jobs.add(key)
        ttl = max(60, int(os.environ.get('FARQ_HARAJ_PUBLIC_REFRESH_SECONDS', '21600')))
        cached = self.harvest.cached(key, variables['page'], ttl)
        if cached is not None:
            return cached
        state = self.harvest.status(key)
        if self.harvest.paused() or state['state'] == 'CHALLENGE':
            raise LiveUnavailable(state['last_error_code'])
        try:
            data = super()._post(variables)
            self.harvest.save(key, variables['page'], data, ttl)
            return data
        except Exception as exc:
            self.harvest.pause(error_code(exc))
            # Preserve observations and last_success. Code only, never provider payloads.
            with self.harvest.transaction() as execute:
                execute('update haraj_public_jobs set state=?,last_error_code=?,next_run=? where id=?',
                        ('CHALLENGE' if error_code(exc) == 'challenge' else 'RATE_LIMITED' if error_code(exc) == 'rate_limited' else 'RETRY', error_code(exc), self.harvest.clock() + 3600, key))
            raise


def harvest_once(harvest, config, budget_seconds=20, max_pages=None):
    """Bounded scheduled slice; persisted work continues on the next cron invocation."""
    max_pages = max(1, min(10, int(os.environ.get("FARQ_HARAJ_HARVEST_PAGES_PER_RUN", "2")))) if max_pages is None else max_pages
    started, count, failed = time.monotonic(), 0, 0
    for _ in range(max_pages):
        if time.monotonic() - started >= budget_seconds:
            break
        job = harvest.claim()
        if not job:
            break
        try:
            client = HarajLiveClient(replace(config, live_timeout_seconds=min(config.live_timeout_seconds, 8)))
            data = client._post({'search': job['query'], 'city': job['city'], 'page': job['next_page'], 'limit': job['page_size']})
            harvest.save(job['id'], job['next_page'], data, max(60, int(os.environ.get('FARQ_HARAJ_PUBLIC_REFRESH_SECONDS', '21600'))), job['lease_token'])
            count += 1
        except Exception as exc:
            harvest.fail(job['id'], job['lease_token'], error_code(exc))
            failed += 1
            break
    return {'pages_saved': count, 'failed': failed}
