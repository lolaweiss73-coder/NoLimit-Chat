import os
import io
from PIL import Image
from fastapi.testclient import TestClient
import app

client = TestClient(app.app)

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
