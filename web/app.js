(() => {
  'use strict';
  const $ = (s, root=document) => root.querySelector(s);
  const area = $('#message-area'), form = $('#message-form'), input = $('#message-input');
  if (!area || !form || !input) return;
  const SESSION_KEY = 'garage_one_session_v1';
  let sessionId = localStorage.getItem(SESSION_KEY);
  if (!sessionId || !/^[A-Za-z0-9_-]{8,64}$/.test(sessionId)) {
    sessionId = (crypto.randomUUID ? crypto.randomUUID() : `go_${Date.now()}_${Math.random().toString(36).slice(2)}`);
    localStorage.setItem(SESSION_KEY, sessionId);
  }
  let lastId = 0, sending = false, pollTimer = null;
  const seenIds = new Set();
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function toast(message, type='') { const region=$('#toast-region'); if(!region)return; const node=document.createElement('div');node.className=`toast ${type}`;node.textContent=message;region.appendChild(node);setTimeout(()=>node.remove(),3600); }
  function timeLabel(date=new Date()) { return date.toLocaleTimeString('vi-VN',{hour:'2-digit',minute:'2-digit'}); }
  function scrollBottom(){
    const move = () => { area.scrollTop = area.scrollHeight; };
    move();
    requestAnimationFrame(move);
  }
  function hideQuickActions(){const q=$('#quick-actions');if(q)q.classList.add('hidden');}
  function appendMessage(role, content, id=null, meta='') {
    if (id && seenIds.has(id)) return;
    if (id) { seenIds.add(id); lastId=Math.max(lastId,Number(id)||0); }
    const row=document.createElement('div');
    if(role==='system') { row.className='message-row system-row'; row.innerHTML=`<div class="message-bubble">${esc(content)}</div>`; }
    else {
      const isUser=role==='user', isStaff=role==='staff';
      row.className=`message-row ${isUser?'user-row':'bot-row'}`;
      const avatar=isUser?'':`<div class="avatar avatar-bot avatar-small" aria-hidden="true"><svg viewBox="0 0 24 24"><rect x="4" y="7" width="16" height="13" rx="4"/><path d="M12 3v4M8 12h.01M16 12h.01M9 16h6"/></svg></div>`;
      const bubbleClass=isUser?'user-bubble':(isStaff?'staff-bubble':'bot-bubble');
      const label=isUser?'Bạn':(isStaff?'Nhân viên hỗ trợ':'Trợ lý AI');
      row.innerHTML=`${avatar}<div class="message-content"><div class="message-bubble ${bubbleClass}">${esc(content).replace(/\n/g,'<br>')}</div><span class="message-meta">${label} · ${timeLabel()}${meta?' · '+esc(meta):''}</span></div>`;
    }
    const stick=role==='user'||area.scrollHeight-area.scrollTop-area.clientHeight<120; area.appendChild(row); if(stick)scrollBottom();
    if(role==='user') { const p=$('#last-preview');if(p)p.textContent=content; }
    return row;
  }
  function setMode(mode) {
    const state=$('#connection-state'), handover=$('#handover-status'), text=$('#handover-text');
    if(mode==='HUMAN_ACTIVE') { if(state)state.innerHTML='Nhân viên hỗ trợ · Đang trực tiếp';if(handover)handover.classList.remove('hidden');if(text)text.textContent='Nhân viên đã tiếp nhận cuộc trò chuyện. Bạn có thể nhắn tin trực tiếp tại đây.'; }
    else if(mode==='HUMAN_PENDING') { if(state)state.innerHTML='Đang chờ nhân viên tiếp nhận';if(handover)handover.classList.remove('hidden');if(text)text.textContent='Yêu cầu của bạn đang chờ nhân viên tiếp nhận. Tin nhắn vẫn được lưu lại.'; }
    else { if(state)state.innerHTML='<span class="online-dot"></span> Trợ lý AI · Trực tuyến';if(handover)handover.classList.add('hidden'); }
  }
  async function pollMessages() {
    try { const r=await fetch(`/chat/${encodeURIComponent(sessionId)}/messages?after=${lastId}`,{headers:{'Accept':'application/json'}});if(!r.ok)return;const data=await r.json();setMode(data.mode);for(const m of (data.messages||[])){appendMessage(m.role==='staff'?'staff':'system',m.content,m.id);} }
    catch (_) { /* Polling is best-effort; keep the composer usable offline. */ }
  }
  function showTyping() { const row=document.createElement('div');row.className='message-row bot-row';row.id='typing-row';row.innerHTML='<div class="avatar avatar-bot avatar-small"><svg viewBox="0 0 24 24"><rect x="4" y="7" width="16" height="13" rx="4"/><path d="M12 3v4M8 12h.01M16 12h.01M9 16h6"/></svg></div><div class="typing-indicator" aria-label="Đang trả lời"><i></i><i></i><i></i></div>';area.appendChild(row);scrollBottom(); }
  form.addEventListener('submit', async e => {
    e.preventDefault(); const message=input.value.trim(); if(!message||sending)return;
    sending=true;$('#send-button').disabled=true;hideQuickActions();appendMessage('user',message);input.value='';input.style.height='auto';showTyping();
    try {
      const r=await fetch('/chat',{method:'POST',headers:{'Content-Type':'application/json','Accept':'application/json'},body:JSON.stringify({session_id:sessionId,message})});
      const data=await r.json().catch(()=>({}));$('#typing-row')?.remove();
      if(!r.ok) throw new Error(data.detail||'Không gửi được tin nhắn. Vui lòng thử lại.');
      if(data.message_id)lastId=Math.max(lastId,Number(data.message_id)||0);
      if(data.reply)appendMessage('ai',data.reply);
      setMode(data.mode);
      if(!data.reply && data.mode!=='BOT') { /* Customer message is saved; human reply arrives through polling. */ }
      const p=$('#last-preview');if(p)p.textContent=data.reply||'Bạn: '+message;
    } catch(err) { toast(err.message||'Không thể kết nối máy chủ. Kiểm tra backend rồi thử lại.','error'); }
    finally { $('#typing-row')?.remove();sending=false;$('#send-button').disabled=false;input.focus({preventScroll:true}); }
  });
  input.addEventListener('input',()=>{input.style.height='auto';input.style.height=Math.min(input.scrollHeight,120)+'px';});
  input.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();form.requestSubmit();}});
  document.querySelectorAll('[data-message]').forEach(button=>button.addEventListener('click',()=>{input.value=button.dataset.message||'';input.focus({preventScroll:true});input.dispatchEvent(new Event('input'));form.requestSubmit();}));
  $('#new-chat')?.addEventListener('click',()=>{if(confirm('Tạo cuộc trò chuyện mới? Lịch sử hiện tại vẫn được lưu trên máy chủ.')){localStorage.removeItem(SESSION_KEY);location.reload();}});
  $('#dismiss-banner')?.addEventListener('click',()=>$('.conversation-banner')?.remove());
  $('#info-toggle')?.addEventListener('click',()=>$('#details-panel')?.classList.toggle('show'));
  $('#close-details')?.addEventListener('click',()=>$('#details-panel')?.classList.remove('show'));
  $('#request-human')?.addEventListener('click',()=>{input.value='Tôi muốn được kết nối với nhân viên tư vấn trực tiếp.';input.focus({preventScroll:true});form.requestSubmit();});
  $('#emoji-button')?.addEventListener('click',()=>{input.value+='😊';input.focus({preventScroll:true});input.dispatchEvent(new Event('input'));});
  pollMessages();pollTimer=setInterval(pollMessages,3000);window.addEventListener('beforeunload',()=>clearInterval(pollTimer));
})();
