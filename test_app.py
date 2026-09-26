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
