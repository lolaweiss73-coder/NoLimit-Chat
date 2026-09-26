import os
import io
import pytest
import zipfile
from backup_data import backup
from PIL import Image
from fastapi.testclient import TestClient
import app

client = TestClient(app.app)

@pytest.fixture(autouse=True)
def fresh_database(tmp_path, monkeypatch):
    monkeypatch.setattr(app, 'DB_PATH', tmp_path / 'chat.db')
    monkeypatch.setattr(app, 'UPLOAD_DIR', tmp_path / 'uploads')
    app.UPLOAD_DIR.mkdir()
    app.init_db()

def test_health():
    r = client.get('/api/health')
    assert r.status_code == 200
    body = r.json()
    assert body['ok'] is True
    assert body['rooms'] >= 3

def test_home():
    r = client.get('/')
    assert r.status_code == 200
    assert "הצ'אט" in r.text

def test_service_worker_does_not_cache_private_api():
    script = client.get('/service-worker.js').text
    assert "CORE.includes(url.pathname)" in script
    assert "caches.delete(key)" in script

def test_reports_require_admin_token(monkeypatch):
    monkeypatch.delenv('CHAT_ADMIN_TOKEN',raising=False)
    assert client.get('/api/admin/reports').status_code==503
    monkeypatch.setenv('CHAT_ADMIN_TOKEN','example-test-secret')
    assert client.get('/api/admin/reports').status_code==403
    assert client.get('/api/admin/reports',headers={'Authorization':'Bearer example-test-secret'}).json()==[]

def test_message_rate_limit():
    for _ in range(20): assert app.hub.allow_message('test-client')
    assert not app.hub.allow_message('test-client')
    app.hub.message_times.pop('test-client')

def test_mentions_respect_room_membership_and_block():
    with client.websocket_connect('/ws') as sender:
        sender.receive_json()
        sender.send_json({'type':'hello','profile':{'nickname':'שולח','age':30,'gender':'male'}})
        while (reply := sender.receive_json())['type'] != 'bootstrap': pass
        sender_id=reply['self']['identity_id']
        con=app.db()
        con.execute("INSERT INTO identities (id,token_hash,nickname,age,gender,created_at) VALUES (?,?,?,?,?,?)",('tag-target','hash-for-test','מקבלת',30,'female',0))
        con.commit();con.close()
        app.hub.profiles['target-client']={'identity_id':'tag-target','nickname':'מקבלת','mention_policy':'all','gender':'female'}
        app.hub.identity_clients['tag-target']='target-client'
        app.hub.room_members['general'].add('target-client')
        try:
            sender.send_json({'type':'room_message','room_id':'general','text':'@מקבלת שלום','mentions':['tag-target']})
            while (reply := sender.receive_json())['type'] != 'room_message': pass
            assert reply['message']['mentions']==['tag-target']
            con=app.db()
            assert con.execute('SELECT COUNT(*) FROM mentions').fetchone()[0]==1
            con.execute('INSERT INTO identity_blocks (blocker_id,blocked_id) VALUES (?,?)',(sender_id,'tag-target'))
            con.commit();con.close()
            sender.send_json({'type':'room_message','room_id':'general','text':'@מקבלת שוב','mentions':['tag-target']})
            while (reply := sender.receive_json())['type'] != 'room_message': pass
            assert reply['message']['mentions']==[]
            con=app.db();assert con.execute('SELECT COUNT(*) FROM mentions').fetchone()[0]==1;con.close()
        finally:
            app.hub.profiles.pop('target-client',None)
            app.hub.identity_clients.pop('tag-target',None)
            app.hub.room_members['general'].discard('target-client')

def test_morin_config_and_isolated_history(monkeypatch):
    tokens=[]
    for nickname in ('אחת','שתיים'):
        with client.websocket_connect('/ws') as ws:
            ws.receive_json()
            ws.send_json({'type':'hello','profile':{'nickname':nickname,'age':30,'gender':'female'}})
            while (reply := ws.receive_json())['type'] != 'bootstrap': pass
            tokens.append(reply['identity_token'])
    headers={'Authorization':'Bearer '+tokens[0]}
    monkeypatch.delenv('OPENROUTER_API_KEY',raising=False)
    assert client.post('/api/morin',json={'message':'שלום'},headers=headers).status_code==503
    monkeypatch.setenv('OPENROUTER_API_KEY','test-key')
    monkeypatch.setenv('MORIN_MODEL','test-model')
    class FakeResponse:
        def raise_for_status(self): pass
        def json(self): return {'choices':[{'message':{'content':'שלום לך'}}]}
    class FakeClient:
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass
        async def post(self,*args,**kwargs):
            assert kwargs['headers']['Authorization']=='Bearer test-key'
            return FakeResponse()
    monkeypatch.setattr(app.httpx,'AsyncClient',lambda **kwargs:FakeClient())
    assert client.post('/api/morin',json={'message':'שלום'},headers=headers).json()['reply']=='שלום לך'
    assert len(client.get('/api/morin/history',headers=headers).json())==2
    assert client.get('/api/morin/history',headers={'Authorization':'Bearer '+tokens[1]}).json()==[]

def test_private_room_rejects_wrong_password():
    import time
    con = app.db()
    con.execute("INSERT INTO rooms (id,name,is_private,password,created_at) VALUES (?,?,?,?,?)",('locked','סגור',1,'secret',time.time()))
    con.commit(); con.close()
    profile={'age':30,'gender':'female','region':'all','relationship_status':'all'}
    assert app.hub.room_allowed('locked',profile,'wrong')[0] is False
    assert app.hub.room_allowed('locked',profile,'secret')[0] is True

def test_backup_contains_database_and_photo(tmp_path):
    with client.websocket_connect('/ws') as ws:
        ws.receive_json()
        ws.send_json({'type':'hello','profile':{'nickname':'גיבוי','age':30,'gender':'female'}})
        while (reply := ws.receive_json())['type'] != 'bootstrap': pass
        token=reply['identity_token']
    picture=io.BytesIO();Image.new('RGB',(8,8),'blue').save(picture,'PNG')
    uploaded=client.post('/api/photos',files={'file':('image.png',picture.getvalue(),'image/png')},headers={'Authorization':'Bearer '+token})
    assert uploaded.status_code==200
    archive=backup(tmp_path,tmp_path.parent/'snapshot.zip')
    with zipfile.ZipFile(archive) as content:
        assert 'chat.db' in content.namelist()
        assert 'uploads/'+uploaded.json()['id'] in content.namelist()

def test_ws_join_and_room_message():
    with client.websocket_connect('/ws') as ws:
        connected = ws.receive_json()
        assert connected['type'] == 'connected'
        ws.send_json({'type':'hello','profile':{'nickname':'בודק','age':30,'gender':'male','interested_in':'female','region':'center','relationship_status':'single','dm_policy':'any'}})
        bootstrap = ws.receive_json()
        while bootstrap['type'] != 'bootstrap':
            bootstrap = ws.receive_json()
        assert bootstrap['active_room'] == 'general'
        ws.send_json({'type':'room_message','room_id':'general','text':'שלום'})
        msg = ws.receive_json()
        while msg['type'] != 'room_message':
            msg = ws.receive_json()
        assert msg['message']['text'] == 'שלום'

def test_dm_gender_policy_and_invalid_age():
    with client.websocket_connect('/ws') as receiver, client.websocket_connect('/ws') as sender:
        receiver_id = receiver.receive_json()['client_id']
        sender.receive_json()
        receiver.send_json({'type':'hello','profile':{'nickname':'מקבלת','age':30,'gender':'female','dm_policy':'female'}})
        while receiver.receive_json()['type'] != 'bootstrap':
            pass
        sender.send_json({'type':'hello','profile':{'nickname':'שולח','age':'invalid','gender':'male'}})
        while sender.receive_json()['type'] != 'error':
            pass
        sender.send_json({'type':'hello','profile':{'nickname':'שולח','age':30,'gender':'male'}})
        while sender.receive_json()['type'] != 'bootstrap':
            pass
        sender.send_json({'type':'dm','target_id':receiver_id,'text':'שלום'})
        while True:
            response = sender.receive_json()
            if response['type'] == 'error':
                assert 'נשים בלבד' in response['message']
                break

def test_offline_message_survives_reconnect():
    with client.websocket_connect('/ws') as recipient:
        recipient.receive_json()
        recipient.send_json({'type':'hello','profile':{'nickname':'נמען','age':31,'gender':'male'}})
        while (reply := recipient.receive_json())['type'] != 'bootstrap':
            pass
        identity_id, token = reply['self']['identity_id'], reply['identity_token']
    with client.websocket_connect('/ws') as sender:
        sender.receive_json()
        sender.send_json({'type':'hello','profile':{'nickname':'כותבת','age':32,'gender':'female'}})
        while sender.receive_json()['type'] != 'bootstrap':
            pass
        sender.send_json({'type':'dm','identity_id':identity_id,'text':'הודעה אחרי ניתוק'})
        while sender.receive_json()['type'] != 'dm':
            pass
    with client.websocket_connect('/ws') as recipient:
        recipient.receive_json()
        recipient.send_json({'type':'hello','identity_token':token,'profile':{'nickname':'נמען','age':31,'gender':'male'}})
        while (reply := recipient.receive_json())['type'] != 'bootstrap':
            pass
        assert reply['self']['identity_id'] == identity_id
        assert any(item['unread'] == 1 for item in reply['inbox'])
        other_id = reply['inbox'][0]['identity_id']
        recipient.send_json({'type':'conversation','identity_id':other_id})
        while (reply := recipient.receive_json())['type'] != 'conversation':
            pass
        assert reply['messages'][-1]['text'] == 'הודעה אחרי ניתוק'
        assert reply['inbox'][0]['unread'] == 0

def test_friend_request_acceptance():
    with client.websocket_connect('/ws') as bob:
        bob.receive_json()
        bob.send_json({'type':'hello','profile':{'nickname':'בוב','age':30,'gender':'male'}})
        while (reply := bob.receive_json())['type'] != 'bootstrap': pass
        bob_id, bob_token = reply['self']['identity_id'], reply['identity_token']
    with client.websocket_connect('/ws') as alice:
        alice.receive_json()
        alice.send_json({'type':'hello','profile':{'nickname':'אליס','age':30,'gender':'female'}})
        while (reply := alice.receive_json())['type'] != 'bootstrap': pass
        alice_id = reply['self']['identity_id']
        alice.send_json({'type':'friend_request','identity_id':bob_id})
        while (reply := alice.receive_json())['type'] != 'friends': pass
        assert reply['friends'][0]['status'] == 'pending'
    with client.websocket_connect('/ws') as bob:
        bob.receive_json()
        bob.send_json({'type':'hello','identity_token':bob_token,'profile':{'nickname':'בוב','age':30,'gender':'male'}})
        while (reply := bob.receive_json())['type'] != 'bootstrap': pass
        assert reply['friends'][0]['incoming'] is True
        bob.send_json({'type':'friend_reply','identity_id':alice_id,'accept':True})
        while (reply := bob.receive_json())['type'] != 'friends' or reply['friends'][0]['status'] != 'accepted': pass
        assert reply['friends'][0]['status'] == 'accepted'

def test_private_photo_only_visible_after_grant():
    identities=[]
    for nickname in ('צלמת','צופה'):
        with client.websocket_connect('/ws') as ws:
            ws.receive_json()
            ws.send_json({'type':'hello','profile':{'nickname':nickname,'age':30,'gender':'female'}})
            while (reply := ws.receive_json())['type'] != 'bootstrap': pass
            identities.append((reply['self']['identity_id'],reply['identity_token']))
    picture=io.BytesIO();Image.new('RGB',(8,8),'blue').save(picture,'PNG')
    owner_id,owner_token=identities[0]
    viewer_id,viewer_token=identities[1]
    uploaded=client.post('/api/photos?visibility=private',files={'file':('image.png',picture.getvalue(),'image/png')},headers={'Authorization':'Bearer '+owner_token})
    assert uploaded.status_code == 200
    photo_id=uploaded.json()['id']
    assert client.get('/api/photos/'+photo_id,headers={'Authorization':'Bearer '+viewer_token}).status_code == 404
    assert client.post(f'/api/photos/{photo_id}/share/{viewer_id}',headers={'Authorization':'Bearer '+viewer_token}).status_code == 404
    assert client.post(f'/api/photos/{photo_id}/share/{viewer_id}',headers={'Authorization':'Bearer '+owner_token}).status_code == 200
    assert client.get('/api/photos/'+photo_id,headers={'Authorization':'Bearer '+viewer_token}).status_code == 200
    assert client.delete(f'/api/photos/{photo_id}/share/{viewer_id}',headers={'Authorization':'Bearer '+viewer_token}).status_code == 404
    assert client.delete(f'/api/photos/{photo_id}/share/{viewer_id}',headers={'Authorization':'Bearer '+owner_token}).status_code == 200
    assert client.get('/api/photos/'+photo_id,headers={'Authorization':'Bearer '+viewer_token}).status_code == 404
    assert client.delete('/api/photos/'+photo_id,headers={'Authorization':'Bearer '+owner_token}).status_code == 200

def test_offline_message_respects_block_and_policy():
    with client.websocket_connect('/ws') as recipient:
        recipient.receive_json()
        recipient.send_json({'type':'hello','profile':{'nickname':'נמען','age':30,'gender':'female','dm_policy':'none'}})
        while (reply := recipient.receive_json())['type'] != 'bootstrap': pass
        recipient_id=reply['self']['identity_id']
    with client.websocket_connect('/ws') as sender:
        sender.receive_json()
        sender.send_json({'type':'hello','profile':{'nickname':'שולח','age':30,'gender':'male'}})
        while (reply := sender.receive_json())['type'] != 'bootstrap': pass
        sender_id=reply['self']['identity_id']
        sender.send_json({'type':'dm','identity_id':recipient_id,'text':'אסור'})
        while (reply := sender.receive_json())['type'] != 'error': pass
        assert 'לא ניתן' in reply['message']
    con=app.db()
    assert con.execute('SELECT COUNT(*) FROM messages').fetchone()[0]==0
    con.execute("UPDATE identities SET dm_policy='any' WHERE id=?",(recipient_id,))
    con.execute('INSERT INTO identity_blocks (blocker_id,blocked_id) VALUES (?,?)',(recipient_id,sender_id))
    con.commit();con.close()
    with client.websocket_connect('/ws') as sender:
        sender.receive_json()
        sender.send_json({'type':'hello','profile':{'nickname':'שולח חדש','age':30,'gender':'male'}})
        while (reply := sender.receive_json())['type'] != 'bootstrap': pass
        new_sender_id=reply['self']['identity_id']
        con=app.db();con.execute('INSERT INTO identity_blocks (blocker_id,blocked_id) VALUES (?,?)',(recipient_id,new_sender_id));con.commit();con.close()
        sender.send_json({'type':'dm','identity_id':recipient_id,'text':'אסור גם עכשיו'})
        while (reply := sender.receive_json())['type'] != 'error': pass
    con=app.db();assert con.execute('SELECT COUNT(*) FROM messages').fetchone()[0]==0;con.close()

def test_visibility_and_distinct_worlds():
    with client.websocket_connect('/ws') as hidden:
        hidden.receive_json()
        hidden.send_json({'type':'hello','profile':{'nickname':'נסתרת','age':30,'gender':'female'}})
        while (reply := hidden.receive_json())['type'] != 'bootstrap': pass
        hidden_id = reply['self']['client_id']
        hidden.send_json({'type':'profile_update','visible_to':'female','worlds':['kinky','soteh']})
        while (reply := hidden.receive_json())['type'] != 'presence': pass
        assert app.hub.profiles[hidden_id]['worlds'] == ['kinky','soteh']
        with client.websocket_connect('/ws') as male:
            male.receive_json()
            male.send_json({'type':'hello','profile':{'nickname':'צופה','age':30,'gender':'male'}})
            while (reply := male.receive_json())['type'] != 'bootstrap': pass
            assert all(u['client_id'] != hidden_id for u in reply['users'])
