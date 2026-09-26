const $ = (s) => document.querySelector(s);
const state = {
  ws: null, clientId: null, self: null, users: [], rooms: [], active: {type:'room', id:'general'},
  messages: new Map(), ignored: new Set(JSON.parse(localStorage.getItem('ignoredUsers') || '[]')),
  blocked: new Set(), dnd: false,
};

const labels = {
  gender: {female:'אישה', male:'גבר', other:'אחר'},
  region: {unspecified:'לא צוין', center:'מרכז', sharon:'שרון', jerusalem:'ירושלים', north:'צפון', south:'דרום', all:'כל הארץ'},
  relationship: {unspecified:'לא צוין', single:'פנוי/ה', relationship:'בזוגיות', married:'נשוי/אה', open:'קשר פתוח', poly:'פוליאמורי/ת', complicated:'מורכב', all:'כולם'},
};

function toast(msg){ const t=$('#toast'); t.textContent=msg; t.classList.add('show'); setTimeout(()=>t.classList.remove('show'),2200); }
function openModal(id){ $('#'+id).classList.add('open'); }
function closeModal(id){ $('#'+id).classList.remove('open'); }
function avatarClass(g){ return g==='female'?'female':g==='male'?'male':'other'; }
function initials(name){ return (name || '?').trim().charAt(0).toUpperCase(); }
function fmtTime(ts){ return new Date(ts*1000).toLocaleTimeString('he-IL',{hour:'2-digit',minute:'2-digit'}); }
function keyForActive(){ return state.active.type+':'+state.active.id; }
function escapeHtml(s){ return String(s).replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c])); }

function loadSaved(){
  const p = JSON.parse(localStorage.getItem('chatProfile') || 'null');
  if(!p) return;
  $('#nickname').value=p.nickname||''; $('#age').value=p.age||''; $('#gender').value=p.gender||'';
  $('#interestedIn').value=p.interested_in||'all'; $('#region').value=p.region||'unspecified'; $('#relationshipStatus').value=p.relationship_status||'unspecified';
}
loadSaved();

function connect(profile){
  const proto = location.protocol==='https:'?'wss':'ws';
  state.ws = new WebSocket(`${proto}://${location.host}/ws`);
  state.ws.onopen=()=>state.ws.send(JSON.stringify({type:'hello',profile}));
  state.ws.onmessage=(e)=>handle(JSON.parse(e.data));
  state.ws.onclose=()=>{ $('#statusDot').classList.remove('online'); toast('החיבור נותק'); };
}

function send(payload){ if(state.ws?.readyState===WebSocket.OPEN) state.ws.send(JSON.stringify(payload)); }

function handle(m){
  if(m.type==='connected'){ state.clientId=m.client_id; $('#statusDot').classList.add('online'); }
  else if(m.type==='bootstrap'){
    state.self=m.self; state.users=m.users; state.rooms=m.rooms; state.active={type:'room',id:m.active_room};
    $('#dmPolicy').value=state.self.dm_policy||'any'; closeModal('gate'); renderAll();
  }
  else if(m.type==='presence'){ state.users=m.users; renderUsers(); $('#onlineCount').textContent=`${state.users.length} מחוברים`; }
  else if(m.type==='rooms'){ state.rooms=m.rooms; renderRooms(); }
  else if(m.type==='joined_room'){ state.active={type:'room',id:m.room_id}; renderConversation(); showPanel('chatPanel'); }
  else if(m.type==='room_created'){ joinRoom(m.room_id); }
  else if(m.type==='room_message'){
    const k='room:'+m.room_id; if(!state.messages.has(k)) state.messages.set(k,[]); state.messages.get(k).push(m.message);
    if(keyForActive()===k) renderMessages();
  }
  else if(m.type==='dm'){
    const other = m.message.from===state.clientId ? m.message.to : m.message.from;
    const k='dm:'+other; if(!state.messages.has(k)) state.messages.set(k,[]); state.messages.get(k).push(m.message);
    if(keyForActive()===k) renderMessages();
    else if(m.message.from!==state.clientId) toast(`הודעה חדשה מ${m.message.profile.nickname}`);
  }
  else if(m.type==='blocked'){ state.blocked.add(m.target_id); closeModal('userModal'); renderUsers(); toast('המשתמש נחסם'); }
  else if(m.type==='blocked_by'){ state.blocked.add(m.target_id); renderUsers(); if(state.active.type==='dm'&&state.active.id===m.target_id){state.active={type:'room',id:'general'};renderConversation();} toast('התקשורת עם משתמש נחסמה'); }
  else if(m.type==='report_received'){ toast('הדיווח התקבל'); closeModal('userModal'); }
  else if(m.type==='error'){ toast(m.message||'אירעה שגיאה'); }
}

function renderAll(){ renderRooms(); renderUsers(); renderConversation(); $('#onlineCount').textContent=`${state.users.length} מחוברים`; }
function renderRooms(){
  $('#roomsList').innerHTML=state.rooms.map(r=>`<div class="room-row" data-room="${r.id}"><div class="row-main"><div class="row-title">${escapeHtml(r.name)} ${r.is_private?'🔒':''}</div><div class="row-sub">${escapeHtml(r.description||'')} · ${r.online||0} מחוברים</div></div><span class="badge">${r.min_age}-${r.max_age}</span></div>`).join('');
  document.querySelectorAll('[data-room]').forEach(el=>el.onclick=()=>joinRoom(el.dataset.room));
}
function sortedUsers(){
  const q=$('#userSearch').value.trim().toLowerCase(); let arr=state.users.filter(u=>u.client_id!==state.clientId&&!state.ignored.has(u.client_id)&&!state.blocked.has(u.client_id));
  if(q) arr=arr.filter(u=>u.nickname.toLowerCase().includes(q));
  const sort=$('#userSort').value; const genderRank=(u,first)=>u.gender===first?0:u.gender==='other'?2:1;
  arr.sort((a,b)=>{
    if(sort==='femaleFirst') return genderRank(a,'female')-genderRank(b,'female') || a.nickname.localeCompare(b.nickname,'he');
    if(sort==='maleFirst') return genderRank(a,'male')-genderRank(b,'male') || a.nickname.localeCompare(b.nickname,'he');
    if(sort==='youngFirst') return a.age-b.age;
    if(sort==='oldFirst') return b.age-a.age;
    if(sort==='newFirst') return b.joined_at-a.joined_at;
    return a.nickname.localeCompare(b.nickname,'he');
  }); return arr;
}
function renderUsers(){
  const arr=sortedUsers();
  $('#usersList').innerHTML=arr.map(u=>`<div class="user-row" data-user="${u.client_id}"><div class="avatar ${avatarClass(u.gender)}">${escapeHtml(initials(u.nickname))}</div><div class="row-main"><div class="row-title">${escapeHtml(u.nickname)} ${u.dnd?'🌙':''}</div><div class="row-sub">${labels.gender[u.gender]} · ${u.age} · ${labels.region[u.region]||u.region}</div></div><span class="badge">${labels.relationship[u.relationship_status]||'לא צוין'}</span></div>`).join('') || '<div class="row-row"><p class="muted" style="padding:16px">אין משתמשים שמתאימים לחיפוש.</p></div>';
  document.querySelectorAll('[data-user]').forEach(el=>el.onclick=()=>openUser(el.dataset.user));
}
function renderConversation(){
  const title=$('#conversationTitle'), meta=$('#conversationMeta'), menu=$('#conversationMenuBtn');
  if(state.active.type==='room'){
    const r=state.rooms.find(x=>x.id===state.active.id); title.textContent=r?.name||'חדר'; meta.textContent=r?.is_private?'חדר פרטי':'חדר ציבורי'; menu.classList.add('hidden');
  }else{
    const u=state.users.find(x=>x.client_id===state.active.id); title.textContent=u?.nickname||'שיחה פרטית'; meta.textContent=u?`${u.age} · ${labels.gender[u.gender]}`:'לא מחובר/ת'; menu.classList.remove('hidden');
  }
  renderMessages();
}
function renderMessages(){
  const arr=state.messages.get(keyForActive())||[];
  $('#messages').innerHTML=arr.map(m=>`<div class="msg ${m.from===state.clientId?'me':''}"><div class="meta">${escapeHtml(m.profile?.nickname||'')} · ${fmtTime(m.ts)}</div><div class="body">${escapeHtml(m.text)}</div></div>`).join('');
  $('#messages').scrollTop=$('#messages').scrollHeight;
}
function joinRoom(id){
  const r=state.rooms.find(x=>x.id===id); let password='';
  if(r?.is_private){ password=prompt('סיסמת החדר')||''; }
  send({type:'join_room',room_id:id,password});
}
function openUser(id){
  const u=state.users.find(x=>x.client_id===id); if(!u)return;
  $('#userCard').innerHTML=`<div class="user-card-head"><div class="avatar ${avatarClass(u.gender)}">${escapeHtml(initials(u.nickname))}</div><div><h2>${escapeHtml(u.nickname)}</h2><div class="muted">${u.age} · ${labels.gender[u.gender]} · ${labels.region[u.region]||u.region}</div></div></div><p>סטטוס: ${labels.relationship[u.relationship_status]||'לא צוין'}${u.dnd?' · נא לא להפריע':''}</p><div class="actions"><button id="dmUser" class="primary">פתח שיחה</button><button id="ignoreUser" class="ghost">התעלם</button><button id="reportUser" class="ghost">דווח</button><button id="blockUser" class="danger">חסום</button></div>`;
  openModal('userModal');
  $('#dmUser').onclick=()=>{state.active={type:'dm',id};closeModal('userModal');renderConversation();showPanel('chatPanel');};
  $('#ignoreUser').onclick=()=>{state.ignored.add(id);localStorage.setItem('ignoredUsers',JSON.stringify([...state.ignored]));closeModal('userModal');renderUsers();toast('המשתמש הוסתר אצלך');};
  $('#blockUser').onclick=()=>send({type:'block',target_id:id});
  $('#reportUser').onclick=()=>{const reason=prompt('סיבת הדיווח')||'';if(reason)send({type:'report',target_id:id,reason,details:''});};
}
function showPanel(id){ document.querySelectorAll('.panel').forEach(p=>p.classList.toggle('active',p.id===id)); document.querySelectorAll('.mobile-tabs button').forEach(b=>b.classList.toggle('active',b.dataset.panel===id)); }

$('#enterBtn').onclick=()=>{
  const p={nickname:$('#nickname').value.trim(),age:Number($('#age').value),gender:$('#gender').value,interested_in:$('#interestedIn').value,region:$('#region').value,relationship_status:$('#relationshipStatus').value,dm_policy:'any',dnd:false};
  if(!$('#adultConfirm').checked){$('#gateError').textContent='צריך לאשר גיל 18+';return;}
  if(!p.nickname||p.age<18||!p.gender){$('#gateError').textContent='צריך כינוי, גיל 18+ ומגדר';return;}
  if($('#rememberMe').checked) localStorage.setItem('chatProfile',JSON.stringify(p)); else localStorage.removeItem('chatProfile');
  connect(p);
};
$('#sendBtn').onclick=()=>{const text=$('#messageInput').value.trim();if(!text)return;if(state.active.type==='room')send({type:'room_message',room_id:state.active.id,text});else send({type:'dm',target_id:state.active.id,text});$('#messageInput').value='';};
$('#messageInput').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();$('#sendBtn').click();}});
$('#userSearch').oninput=renderUsers; $('#userSort').onchange=renderUsers;
$('#settingsBtn').onclick=()=>openModal('settingsModal');
$('#dmPolicy').onchange=()=>send({type:'profile_update',dm_policy:$('#dmPolicy').value});
$('#dndBtn').onclick=()=>{state.dnd=!state.dnd;$('#dndBtn').textContent=`נא לא להפריע: ${state.dnd?'פעיל':'כבוי'}`;send({type:'profile_update',dnd:state.dnd});};
$('#newRoomBtn').onclick=()=>openModal('roomModal');
$('#roomPrivate').onchange=()=>$('#roomPasswordWrap').classList.toggle('hidden',!$('#roomPrivate').checked);
$('#createRoomBtn').onclick=()=>{send({type:'create_room',room:{name:$('#roomName').value.trim(),description:$('#roomDescription').value.trim(),allowed_gender:$('#roomGender').value,min_age:Number($('#roomMinAge').value||18),max_age:Number($('#roomMaxAge').value||99),region:$('#roomRegion').value,relationship_status:$('#roomRelationship').value,is_private:$('#roomPrivate').checked,password:$('#roomPassword').value}});closeModal('roomModal');};
$('#conversationMenuBtn').onclick=()=>state.active.type==='dm'&&openUser(state.active.id);
document.querySelectorAll('[data-close]').forEach(b=>b.onclick=()=>closeModal(b.dataset.close));
document.querySelectorAll('.mobile-tabs button').forEach(b=>b.onclick=()=>showPanel(b.dataset.panel));

if('serviceWorker' in navigator){navigator.serviceWorker.register('/service-worker.js').catch(()=>{});}
