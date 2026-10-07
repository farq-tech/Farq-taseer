import json
from dataclasses import replace
from farq.config import SearchConfig
from farq.haraj_harvest import CachedHarajClient, HarvestStore, harvest_once
from farq.live_haraj import HarajLiveClient, LiveUnavailable
from farq.store import Store


def item(page):
    return {'items':[{'id':str(page), 'title':'سباك', 'authorId':'supplier', 'bodyTEXT':'صيانة', 'status':True,
                     'accessToken':'synthetic-secret-must-not-persist', 'price':{'inputPrice':'100','refreshToken':'secret'}}],
            'pageInfo':{'hasNextPage':page < 4}}


def test_background_resumes_without_browser_and_load_more_uses_saved_details(tmp_path, monkeypatch):
    store = Store(tmp_path/'db', tmp_path/'uploads')
    now = [1000.0]
    harvest = HarvestStore(store, clock=lambda:now[0])
    calls = []
    def post(self, variables):
        calls.append(variables['page'])
        return item(variables['page'])
    monkeypatch.setattr(HarajLiveClient, '_post', post)
    config = SearchConfig(live_max_pages=1)
    client = CachedHarajClient(config, harvest)
    assert client.search_one('سباك', 'الرياض').pages == 1
    key = next(iter(client.jobs))
    # A brand new process/store resumes after the user closes the screen.
    now[0] += 60
    resumed = HarvestStore(Store(tmp_path/'db', tmp_path/'uploads'), clock=lambda:now[0])
    assert harvest_once(resumed, config)['pages_saved'] == 1
    now[0] += 60
    assert harvest_once(resumed, config)['pages_saved'] == 1
    later = CachedHarajClient(replace(config, live_start_page=2), resumed)
    assert later.search_one('سباك', 'الرياض').ads[0].description == 'صيانة'
    assert calls == [1,2,3]  # load more didn't call Haraj again
    with resumed.transaction() as execute:
        raw = execute('select payload from haraj_public_pages where job_id=? and page=1', (key,)).fetchone()['payload']
        assert 'synthetic-secret' not in raw and 'refreshToken' not in raw
        assert execute('select count(*) as n from haraj_public_ads').fetchone()['n'] == 3
    now[0] += 60
    harvest_once(resumed, config)
    assert resumed.status(key)['state'] == 'COMPLETE'


def test_failed_page_keeps_cursor_details_and_last_success(tmp_path, monkeypatch):
    now=[1000.0]
    harvest=HarvestStore(Store(tmp_path/'db',tmp_path/'uploads'),lambda:now[0])
    key=harvest.ensure('سباك','الرياض',20)
    harvest.save(key,1,item(1))
    successful=harvest.status(key)['last_success']
    now[0]+=60
    monkeypatch.setattr(HarajLiveClient,'_post',lambda *args: (_ for _ in ()).throw(LiveUnavailable('synthetic outage')))
    assert harvest_once(harvest,SearchConfig())['failed']==1
    status=harvest.status(key)
    assert status['next_page']==2 and status['state']=='RETRY'
    assert status['last_success']==successful
    assert harvest.cached(key,1,21600)['items'][0]['id']=='1'


def test_lease_fencing_and_expiry(tmp_path):
    now=[1000.0]
    harvest=HarvestStore(Store(tmp_path/'db',tmp_path/'uploads'),lambda:now[0])
    key=harvest.ensure('سباك',None,20)
    old=harvest.claim()
    assert harvest.claim() is None
    now[0]+=91
    new=harvest.claim()
    assert new['lease_token']!=old['lease_token']
    assert not harvest.save(key,1,item(1),lease=old['lease_token'])
    assert harvest.save(key,1,item(1),lease=new['lease_token'])
    assert harvest.status(key)['next_page']==2


def test_rate_limit_and_challenge_preserve_data_and_pause(tmp_path,monkeypatch):
    now=[1000.0]
    harvest=HarvestStore(Store(tmp_path/'db',tmp_path/'uploads'),lambda:now[0])
    key=harvest.ensure('سباك',None,20)
    harvest.save(key,1,item(1)); now[0]+=60
    calls=[]
    def blocked(self,variables):
        calls.append(variables['page']); raise LiveUnavailable('HTTP Error 429: Too Many Requests')
    monkeypatch.setattr(HarajLiveClient,'_post',blocked)
    harvest_once(harvest,SearchConfig()); harvest_once(harvest,SearchConfig())
    assert calls==[2] and harvest.status(key)['state']=='RATE_LIMITED'
    harvest.ensure('كهربائي',None,20)
    assert harvest.claim() is None  # provider pause also stops other public queries
    now[0]+=3601
    def challenge(self,variables):
        raise LiveUnavailable('HTTP Error 403: challenge')
    monkeypatch.setattr(HarajLiveClient,'_post',challenge)
    harvest_once(harvest,SearchConfig()); now[0]+=86400
    assert harvest.claim() is None
    assert harvest.paused()
    assert harvest.cached(key,1,999999) is not None


def test_invalid_observation_cannot_erase_known_details(tmp_path):
    import pytest
    harvest=HarvestStore(Store(tmp_path/'db',tmp_path/'uploads'))
    key=harvest.ensure('سباك',None,20)
    harvest.save(key,1,item(1))
    before=harvest.status(key)
    with pytest.raises(LiveUnavailable):
        harvest.save(key,2,{'items':[]})
    assert harvest.status(key)==before
    assert harvest.cached(key,1,21600)['items'][0]['bodyTEXT']=='صيانة'


def test_availability_is_owned_and_cron_requires_secret(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from farq.api import create_app
    from farq.corpus import MemoryCorpus
    monkeypatch.setenv('CRON_SECRET','synthetic-cron-key')
    monkeypatch.setenv('FARQ_HARAJ_PUBLIC_HARVEST_ENABLED','1')
    monkeypatch.setattr(HarajLiveClient,'_post',lambda self,variables:item(variables['page']))
    config=SearchConfig(live_max_pages=1,live_max_queries=1)
    api=TestClient(create_app(Store(tmp_path/'db',tmp_path/'uploads'),MemoryCorpus([]),HarajLiveClient(config),config))
    a=api.post('/v1/auth/register',json={'email':'a@example.com','password':'synthetic-test-pass','name':'User A'}).json()['token']
    b=api.post('/v1/auth/register',json={'email':'b@example.com','password':'synthetic-test-pass','name':'User B'}).json()['token']
    first=api.post('/v1/search',headers={'Authorization':'Bearer '+a},json={'query':'سباك بالرياض','page':1}).json()
    path='/v1/search/'+first['trace_id']+'/availability'
    assert api.get(path,headers={'Authorization':'Bearer '+a}).json()['state']=='FETCHING'
    assert api.get(path,headers={'Authorization':'Bearer '+b}).status_code==404
    assert api.get(path).status_code==404
    assert api.get('/v1/internal/haraj-public-harvest').status_code==401
    assert api.get('/v1/internal/haraj-public-harvest',headers={'Authorization':'Bearer synthetic-cron-key'}).status_code==200
