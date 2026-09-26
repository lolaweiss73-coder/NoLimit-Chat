const $=selector=>document.querySelector(selector);
let token=sessionStorage.getItem('noShameSession');
function showStatus(message){$('#youthStatus').textContent=message;}
async function loadPosts(){
  const response=await fetch('/api/youth/posts',{headers:{Authorization:'Bearer '+token}});
  if(!response.ok){showStatus('לא ניתן לטעון את השיחות');return;}
  $('#youthPosts').replaceChildren();
  for(const post of await response.json()){
    const item=document.createElement('p');item.className='msg';item.textContent=post.text;$('#youthPosts').append(item);
  }
}
async function enter(){
  $('#joinSection').classList.add('hidden');$('#youthForum').classList.remove('hidden');
  const response=await fetch('/api/youth/posts',{headers:{Authorization:'Bearer '+token}});
  if(response.status===401){sessionStorage.removeItem('noShameSession');location.reload();return;}
  if(response.ok){$('#bandLabel').textContent='קבוצת הגיל שלך: '+(sessionStorage.getItem('noShameBand')||'');loadPosts();}
}
$('#joinYouth').onclick=async()=>{
  const age=Number($('#youthAge').value);
  if(!Number.isInteger(age)||age<13||age>17){alert('הכניסה מיועדת לגילאי 13 עד 17');return;}
  const response=await fetch('/api/youth/join',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({age})});
  if(!response.ok){alert('האזור עדיין אינו פתוח');return;}
  const result=await response.json();token=result.token;sessionStorage.setItem('noShameSession',token);sessionStorage.setItem('noShameBand',result.age_band);enter();
};
$('#publishYouth').onclick=async()=>{
  const text=$('#youthText').value.trim();if(!text)return;
  const response=await fetch('/api/youth/posts',{method:'POST',headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'},body:JSON.stringify({text})});
  if(response.ok){const result=await response.json();showStatus(result.message);$('#youthText').value='';if(result.status==='visible')loadPosts();}
  else showStatus('הפרסום לא נקלט. נסה שוב מאוחר יותר.');
};
if(token)enter();
