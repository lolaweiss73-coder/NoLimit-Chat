const $ = (s) => document.querySelector(s);
const state = {
  ws: null, clientId: null, self: null, users: [], rooms: [], active: {type:'room', id:'general'},
  messages: new Map(), ignored: new Set(JSON.parse(localStorage.getItem('ignoredUsers') || '[]')),
  blocked: new Set(), dnd: false, inbox: [], friends: [], mentions: [], composingMentions: [],
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
function authHeaders(){return {Authorization:'Bearer '+localStorage.getItem('chatIdentityToken')};}
async function showPhotos(ownerId,targetId,shareTo){
  const target=$('#'+targetId); if(!target)return;
  try{
    const response=await fetch('/api/profiles/'+encodeURIComponent(ownerId)+'/photos',{headers:authHeaders()});
    if(!response.ok)throw Error('photos');
    const photos=await response.json(); target.replaceChildren();
    for(const p of photos){
      const imageResponse=await fetch('/api/photos/'+p.id,{headers:authHeaders()});
      if(!imageResponse.ok)continue;
      const image=document.createElement('img'); image.src=URL.createObjectURL(await imageResponse.blob());image.width=90;image.alt='תמונת פרופיל';image.onload=()=>URL.revokeObjectURL(image.src);target.append(image);
      if(shareTo&&p.visibility==='private'){
        const button=document.createElement('button');button.textContent='שתף תמונה זו';button.className='ghost';
        button.onclick=async()=>{const result=await fetch(`/api/photos/${p.id}/share/${encodeURIComponent(shareTo)}`,{method:'POST',headers:authHeaders()});toast(result.ok?'התמונה שותפה':'השיתוף נכשל');};target.append(button);
        const revoke=document.createElement('button');revoke.textContent='בטל שיתוף';revoke.className='ghost';revoke.onclick=async()=>{const result=await fetch(`/api/photos/${p.id}/share/${encodeURIComponent(shareTo)}`,{method:'DELETE',headers:authHeaders()});toast(result.ok?'השיתוף בוטל':'הביטול נכשל');};target.append(revoke);
      }
      if(ownerId===state.self?.identity_id&&!shareTo){
        const remove=document.createElement('button');remove.textContent='מחק תמונה';remove.className='danger';remove.onclick=async()=>{if(!confirm('למחוק את התמונה?'))return;const result=await fetch('/api/photos/'+p.id,{method:'DELETE',headers:authHeaders()});if(result.ok)showPhotos(ownerId,targetId);};target.append(remove);
      }
    }
  }catch{target.textContent='לא ניתן לטעון תמונות';}
}
function escapeHtml(s){ return String(s).replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c])); }
function formatText(value){
  return escapeHtml(value).replace(/https?:\/\/[^\s&lt;&gt;]+/g,raw=>{
    try{const url=new URL(raw.replace(/&amp;/g,'&'));if(!['http:','https:'].includes(url.protocol))return raw;return `<a href="${escapeHtml(url.href)}" target="_blank" rel="noopener noreferrer">${raw}</a>`;}catch{return raw;}
  });
}
function youtubePreview(text){
  const match=String(text).match(/https?:\/\/(?:www\.)?(?:youtube\.com\/watch\?[^\s]*v=|youtu\.be\/)([A-Za-z0-9_-]{11})/);
  return match?`<button class="ghost" data-youtube="${match[1]}">הצג סרטון YouTube</button>`:'';
}

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
  state.ws.onopen=()=>state.ws.send(JSON.stringify({type:'hello',profile,identity_token:localStorage.getItem('chatIdentityToken')}));
  state.ws.onmessage=(e)=>handle(JSON.parse(e.data));
  state.ws.onclose=()=>{ $('#statusDot').classList.remove('online'); toast('החיבור נותק'); };
}

function send(payload){ if(state.ws?.readyState===WebSocket.OPEN) state.ws.send(JSON.stringify(payload)); }

function handle(m){
  if(m.type==='connected'){ state.clientId=m.client_id; $('#statusDot').classList.add('online'); }
  else if(m.type==='bootstrap'){
    state.self=m.self; state.users=m.users; state.rooms=m.rooms; state.inbox=m.inbox||[]; state.friends=m.friends||[];state.mentions=m.mentions||[]; state.active={type:'room',id:m.active_room};
    localStorage.setItem('chatIdentityToken',m.identity_token);
    $('#dmPolicy').value=state.self.dm_policy||'any'; $('#profileDescription').value=state.self.description||'';$('#visibleTo').value=state.self.visible_to||'all';$('#mentionPolicy').value=state.self.mention_policy||'all';document.querySelectorAll('[name=world]').forEach(el=>el.checked=(state.self.worlds||[]).includes(el.value));closeModal('gate'); renderAll();
  }
  else if(m.type==='presence'){ state.users=m.users; renderUsers(); renderInbox(); $('#onlineCount').textContent=`${state.users.length} מחוברים`; }
  else if(m.type==='rooms'){ state.rooms=m.rooms; renderRooms(); }
  else if(m.type==='joined_room'){ state.active={type:'room',id:m.room_id}; renderConversation(); showPanel('chatPanel'); }
  else if(m.type==='room_created'){ joinRoom(m.room_id); }
  else if(m.type==='room_message'){
    const k='room:'+m.room_id; if(!state.messages.has(k)) state.messages.set(k,[]); state.messages.get(k).push(m.message);
    if(keyForActive()===k) renderMessages();
  }
  else if(m.type==='dm'){
    if(m.inbox) { state.inbox=m.inbox; renderInbox(); }
    const other = m.message.from_identity===state.self.identity_id ? m.message.to_identity : m.message.from_identity;
    const k='dm:'+other; if(!state.messages.has(k)) state.messages.set(k,[]); state.messages.get(k).push(m.message);
    if(keyForActive()===k) renderMessages();
    else if(m.message.from_identity!==state.self.identity_id) toast(`הודעה חדשה מ${m.message.profile.nickname}`);
  }
  else if(m.type==='conversation'){state.messages.set('dm:'+m.identity_id,m.messages);state.inbox=m.inbox;renderInbox();if(keyForActive()==='dm:'+m.identity_id)renderMessages();}
  else if(m.type==='read_receipt'){state.messages.set('dm:'+m.identity_id,m.messages);if(keyForActive()==='dm:'+m.identity_id)renderMessages();}
  else if(m.type==='typing'&&keyForActive()==='dm:'+m.identity_id){$('#conversationMeta').textContent='מקליד/ה...';setTimeout(()=>renderConversation(),1800);}
  else if(m.type==='friends'){state.friends=m.friends;renderFriends();}
  else if(m.type==='mentions'){state.mentions=m.mentions;renderMentions();if(m.mentions.some(i=>!i.read_at))toast('תיוג חדש בחדר');}
  else if(m.type==='blocked'){ state.blocked.add(m.target_id); closeModal('userModal'); renderUsers(); toast('המשתמש נחסם'); }
  else if(m.type==='blocked_by'){ state.blocked.add(m.target_id); renderUsers(); if(state.active.type==='dm'&&state.active.id===m.target_id){state.active={type:'room',id:'general'};renderConversation();} toast('התקשורת עם משתמש נחסמה'); }
  else if(m.type==='report_received'){ toast('הדיווח התקבל'); closeModal('userModal'); }
  else if(m.type==='error'){ toast(m.message||'אירעה שגיאה'); }
}

function renderAll(){ renderRooms(); renderUsers(); renderInbox(); renderFriends(); renderMentions(); renderConversation(); $('#onlineCount').textContent=`${state.users.length} מחוברים`; }
function renderMentions(){
  $('#mentionsList').innerHTML=state.mentions.map(m=>`<div class="user-row" data-mentioned-room="${escapeHtml(m.room_id)}"><span>${escapeHtml(m.nickname)}: ${escapeHtml(m.text.slice(0,80))}</span>${!m.read_at?'<span class="badge">חדש</span>':''}</div>`).join('')||'<p class="muted" style="padding:12px">אין תיוגים.</p>';
  document.querySelectorAll('[data-mentioned-room]').forEach(el=>el.onclick=()=>{joinRoom(el.dataset.mentionedRoom);send({type:'read_mentions'});});
}
function renderFriends(){
  $('#friendsList').innerHTML=state.friends.filter(f=>f.status!=='rejected').map(f=>`<div class="user-row"><span>${escapeHtml(f.nickname)} · ${f.status==='accepted'?'חברים':f.incoming?'בקשה נכנסת':'בקשה נשלחה'}</span>${f.status==='pending'&&f.incoming?`<button class="primary" data-accept="${escapeHtml(f.identity_id)}">אישור</button><button class="ghost" data-reject="${escapeHtml(f.identity_id)}">דחייה</button>`:f.status==='accepted'?`<button class="ghost" data-friend-chat="${escapeHtml(f.identity_id)}">שיחה</button>`:''}<button class="ghost" data-friend-remove="${escapeHtml(f.identity_id)}">הסר</button></div>`).join('')||'<p class="muted" style="padding:12px">עדיין אין חברים.</p>';
  document.querySelectorAll('[data-accept]').forEach(el=>el.onclick=()=>send({type:'friend_reply',identity_id:el.dataset.accept,accept:true}));
  document.querySelectorAll('[data-reject]').forEach(el=>el.onclick=()=>send({type:'friend_reply',identity_id:el.dataset.reject,accept:false}));
  document.querySelectorAll('[data-friend-chat]').forEach(el=>el.onclick=()=>openConversation(el.dataset.friendChat));
  document.querySelectorAll('[data-friend-remove]').forEach(el=>el.onclick=()=>send({type:'friend_remove',identity_id:el.dataset.friendRemove}));
}
function renderInbox(){
  $('#inboxList').innerHTML=state.inbox.map(i=>`<div class="user-row" data-inbox="${escapeHtml(i.identity_id)}"><div class="row-main"><div class="row-title">${escapeHtml(i.nickname)}</div></div>${i.unread?`<span class="badge">${i.unread} חדשות</span>`:''}</div>`).join('')||'<p class="muted" style="padding:12px">עדיין אין הודעות.</p>';
  document.querySelectorAll('[data-inbox]').forEach(el=>el.onclick=()=>openConversation(el.dataset.inbox));
}
function openConversation(id){state.active={type:'dm',id};send({type:'conversation',identity_id:id});renderConversation();showPanel('chatPanel');}
function renderRooms(){
  $('#roomsList').innerHTML=state.rooms.map(r=>`<div class="room-row" data-room="${r.id}"><div class="row-main"><div class="row-title">${escapeHtml(r.name)} ${r.is_private?'🔒':''}</div><div class="row-sub">${escapeHtml(r.description||'')} · ${r.online||0} מחוברים</div></div><span class="badge">${r.min_age}-${r.max_age}</span></div>`).join('');
  document.querySelectorAll('[data-room]').forEach(el=>el.onclick=()=>joinRoom(el.dataset.room));
}
function sortedUsers(){
  const q=$('#userSearch').value.trim().toLowerCase(); let arr=state.users.filter(u=>u.client_id!==state.clientId&&!state.ignored.has(u.client_id)&&!state.blocked.has(u.client_id));
  const world=$('#worldFilter').value;if(world!=='all')arr=arr.filter(u=>(u.worlds||[]).includes(world));
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
    const u=state.users.find(x=>x.identity_id===state.active.id), saved=state.inbox.find(x=>x.identity_id===state.active.id); title.textContent=u?.nickname||saved?.nickname||'שיחה פרטית'; meta.textContent=u?`${u.age} · ${labels.gender[u.gender]}`:'לא מחובר/ת'; menu.classList.toggle('hidden',!u);
  }
  renderMessages();
}
function renderMessages(){
  const arr=state.messages.get(keyForActive())||[];
  $('#messages').innerHTML=arr.map(m=>`<div class="msg ${(m.from_identity?m.from_identity===state.self?.identity_id:m.from===state.clientId)?'me':''}"><div class="meta">${escapeHtml(m.profile?.nickname||'')} · ${fmtTime(m.ts)} ${m.read_at&&m.from_identity===state.self?.identity_id?'· נקראה':''}</div><div class="body">${formatText(m.text)}</div>${youtubePreview(m.text)}${m.contact_card?`<div class="badge">${escapeHtml(m.contact_card.kind)}: ${escapeHtml(m.contact_card.value)}</div>`:''}${m.photo_id?`<button class="ghost" data-photo="${escapeHtml(m.photo_id)}">הצג תמונה</button>`:''}</div>`).join('');
  document.querySelectorAll('[data-youtube]').forEach(button=>button.onclick=()=>{const frame=document.createElement('iframe');frame.src='https://www.youtube-nocookie.com/embed/'+button.dataset.youtube;frame.title='YouTube';frame.allowFullscreen=true;button.replaceWith(frame);});
  document.querySelectorAll('[data-photo]').forEach(button=>button.onclick=async()=>{const response=await fetch('/api/photos/'+button.dataset.photo,{headers:authHeaders()});if(response.ok){const image=document.createElement('img');image.src=URL.createObjectURL(await response.blob());image.style.maxWidth='100%';image.onload=()=>URL.revokeObjectURL(image.src);button.replaceWith(image);}});
  $('#messages').scrollTop=$('#messages').scrollHeight;
}
function joinRoom(id){
  state.composingMentions=[];$('#mentionSuggestions').classList.add('hidden');
  const r=state.rooms.find(x=>x.id===id); let password='';
  if(r?.is_private){ password=prompt('סיסמת החדר')||''; }
  send({type:'join_room',room_id:id,password});
}
function openUser(id){
  const u=state.users.find(x=>x.client_id===id); if(!u)return;
  $('#userCard').innerHTML=`<div class="user-card-head"><div class="avatar ${avatarClass(u.gender)}">${escapeHtml(initials(u.nickname))}</div><div><h2>${escapeHtml(u.nickname)}</h2><div class="muted">${u.age} · ${labels.gender[u.gender]} · ${labels.region[u.region]||u.region}</div></div></div><p>${escapeHtml(u.description||'')}</p><p>${(u.worlds||[]).map(w=>({vanilla:'ונילה',kinky:'קינקי',soteh:'סוטה'}[w])).join(' · ')}</p><p>סטטוס: ${labels.relationship[u.relationship_status]||'לא צוין'}${u.dnd?' · נא לא להפריע':''}</p><div class="actions"><button id="dmUser" class="primary">פתח שיחה</button><button id="friendUser" class="ghost">בקשת חברות</button><button id="ignoreUser" class="ghost">התעלם</button><button id="reportUser" class="ghost">דווח</button><button id="blockUser" class="danger">חסום</button></div>`;
  openModal('userModal');
  const photoSection=document.createElement('div');photoSection.id='userPhotos';$('#userCard').prepend(photoSection);showPhotos(u.identity_id,'userPhotos');
  const shareButton=document.createElement('button');shareButton.className='ghost';shareButton.textContent='שתף תמונה פרטית שלי';shareButton.onclick=()=>{
    let area=$('#sharePhotos');if(!area){area=document.createElement('div');area.id='sharePhotos';$('#userCard').prepend(area);}showPhotos(state.self.identity_id,'sharePhotos',u.identity_id);
  };$('#userCard').prepend(shareButton);
  $('#dmUser').onclick=()=>{closeModal('userModal');openConversation(u.identity_id);};
  $('#friendUser').onclick=()=>{send({type:'friend_request',identity_id:u.identity_id});closeModal('userModal');};
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
$('#sendBtn').onclick=()=>{const text=$('#messageInput').value.trim();if(!text)return;if(state.active.type==='room')send({type:'room_message',room_id:state.active.id,text,mentions:state.composingMentions.filter(m=>text.includes('@'+m.nickname)).map(m=>m.identity_id)});else send({type:'dm',identity_id:state.active.id,text});$('#messageInput').value='';state.composingMentions=[];$('#mentionSuggestions').classList.add('hidden');};
$('#messageInput').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();$('#sendBtn').click();}});
let lastTyping=0;$('#messageInput').addEventListener('input',()=>{
  if(state.active.type==='dm'&&Date.now()-lastTyping>1500){send({type:'typing',identity_id:state.active.id});lastTyping=Date.now();}
  const input=$('#messageInput'),prefix=input.value.slice(0,input.selectionStart),match=state.active.type==='room'?prefix.match(/@([^\s@]{0,40})$/):null;
  const suggestions=$('#mentionSuggestions');suggestions.replaceChildren();suggestions.classList.toggle('hidden',!match);
  if(!match)return;
  for(const user of state.users.filter(u=>u.client_id!==state.clientId&&u.current_room===state.active.id&&u.nickname.includes(match[1])).slice(0,6)){
    const button=document.createElement('button');button.className='ghost';button.textContent=user.nickname;
    button.onclick=()=>{const start=input.selectionStart-match[0].length;input.setRangeText('@'+user.nickname+' ',start,input.selectionStart,'end');state.composingMentions.push({identity_id:user.identity_id,nickname:user.nickname});suggestions.classList.add('hidden');input.focus();};suggestions.append(button);
  }
});
$('#roomPhotoInput').onchange=async()=>{const file=$('#roomPhotoInput').files[0];if(!file||state.active.type!=='room')return;const form=new FormData();form.append('file',file);const response=await fetch('/api/photos?visibility=public',{method:'POST',headers:authHeaders(),body:form});if(response.ok){const photo=await response.json();send({type:'room_message',room_id:state.active.id,text:$('#messageInput').value.trim(),photo_id:photo.id});$('#messageInput').value='';}else toast('העלאת התמונה נכשלה');$('#roomPhotoInput').value='';};
$('#contactCardBtn').onclick=()=>{if(state.active.type!=='dm'){toast('כרטיס קשר נשלח בשיחה פרטית');return;}const kind=prompt('סוג: phone, whatsapp, telegram, email, website');if(!['phone','whatsapp','telegram','email','website'].includes(kind))return;const value=prompt('פרט הקשר');if(value)send({type:'dm',identity_id:state.active.id,contact_card:{kind,value}});};
function appendMorin(role,message){const item=document.createElement('div');item.className='msg '+(role==='user'?'me':'');item.textContent=(role==='assistant'?'מורין: ':'אני: ')+message;$('#morinHistory').append(item);$('#morinHistory').scrollTop=$('#morinHistory').scrollHeight;}
$('#morinBtn').onclick=async()=>{openModal('morinModal');$('#morinHistory').replaceChildren();try{const r=await fetch('/api/morin/history',{headers:authHeaders()});if(r.ok)for(const m of await r.json())appendMorin(m.role,m.text);}catch{toast('לא ניתן לטעון שיחה');}};
$('#morinSend').onclick=async()=>{const input=$('#morinInput'),message=input.value.trim();if(!message)return;input.value='';appendMorin('user',message);$('#morinSend').disabled=true;try{const response=await fetch('/api/morin',{method:'POST',headers:{...authHeaders(),'Content-Type':'application/json'},body:JSON.stringify({message})});if(!response.ok)throw Error(response.status===503?'מורין עדיין לא מחוברת לספק AI':'מורין אינה זמינה כרגע');appendMorin('assistant',(await response.json()).reply);}catch(error){toast(error.message);}finally{$('#morinSend').disabled=false;}};
$('#userSearch').oninput=renderUsers; $('#userSort').onchange=renderUsers;
$('#worldFilter').onchange=renderUsers;
$('#settingsBtn').onclick=()=>openModal('settingsModal');
$('#dmPolicy').onchange=()=>send({type:'profile_update',dm_policy:$('#dmPolicy').value});
$('#saveProfileBtn').onclick=()=>{send({type:'profile_update',description:$('#profileDescription').value,visible_to:$('#visibleTo').value,mention_policy:$('#mentionPolicy').value,worlds:[...document.querySelectorAll('[name=world]:checked')].map(el=>el.value)});closeModal('settingsModal');};
$('#uploadPhotoBtn').onclick=async()=>{const file=$('#photoUpload').files[0];if(!file)return;const form=new FormData();form.append('file',file);const result=await fetch('/api/photos?visibility='+$('#photoVisibility').value,{method:'POST',headers:authHeaders(),body:form});toast(result.ok?'התמונה עלתה':'העלאה נכשלה');if(result.ok)showPhotos(state.self.identity_id,'myPhotos');};
$('#settingsBtn').onclick=()=>{openModal('settingsModal');if(state.self)showPhotos(state.self.identity_id,'myPhotos');};
$('#dndBtn').onclick=()=>{state.dnd=!state.dnd;$('#dndBtn').textContent=`נא לא להפריע: ${state.dnd?'פעיל':'כבוי'}`;send({type:'profile_update',dnd:state.dnd});};
$('#newRoomBtn').onclick=()=>openModal('roomModal');
$('#roomPrivate').onchange=()=>$('#roomPasswordWrap').classList.toggle('hidden',!$('#roomPrivate').checked);
$('#createRoomBtn').onclick=()=>{send({type:'create_room',room:{name:$('#roomName').value.trim(),description:$('#roomDescription').value.trim(),allowed_gender:$('#roomGender').value,min_age:Number($('#roomMinAge').value||18),max_age:Number($('#roomMaxAge').value||99),region:$('#roomRegion').value,relationship_status:$('#roomRelationship').value,is_private:$('#roomPrivate').checked,password:$('#roomPassword').value}});closeModal('roomModal');};
$('#conversationMenuBtn').onclick=()=>{const u=state.users.find(x=>x.identity_id===state.active.id);if(u)openUser(u.client_id);};
document.querySelectorAll('[data-close]').forEach(b=>b.onclick=()=>closeModal(b.dataset.close));
document.querySelectorAll('.mobile-tabs button').forEach(b=>b.onclick=()=>showPanel(b.dataset.panel));

if('serviceWorker' in navigator){navigator.serviceWorker.register('/service-worker.js').catch(()=>{});}
fetch('/api/youth/status').then(r=>r.json()).then(data=>$('#youthLinkWrap').classList.toggle('hidden',!data.enabled)).catch(()=>{});
