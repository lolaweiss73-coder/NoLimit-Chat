import os
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
