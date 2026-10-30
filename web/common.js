const $ = (s, r = document) => r.querySelector(s);
const el = (t, c, x) => { const e = document.createElement(t); if (c) e.className = c; if (x != null) e.textContent = x; return e; };
const hm = ts => new Date(ts).toLocaleTimeString("vi-VN", { hour: "2-digit", minute: "2-digit" });
const icon = n => { const s = document.createElementNS("http://www.w3.org/2000/svg", "svg"); s.setAttribute("class", "ic"); s.setAttribute("aria-hidden", "true"); s.innerHTML = `<use href="#i-${n}"/>`; return s; };

function body(text) {
  const f = document.createDocumentFragment(); let ul = null;
  for (const ln of String(text).split("\n")) {
    const m = ln.match(/^\s*[-•]\s+(.*)/);
    if (m) { if (!ul) { ul = el("ul"); f.append(ul); } ul.append(el("li", "", m[1])); }
    else { ul = null; if (ln.trim()) f.append(el("p", "", ln)); }
  }
  return f;
}

async function api(path, opt) {
  const r = await fetch(path, opt); let j = null;
  try { j = await r.json(); } catch {}
  if (!r.ok) throw Object.assign(new Error((j && j.detail) || r.statusText), { status: r.status });
  return j;
}

document.body.insertAdjacentHTML("afterbegin", `<svg width="0" height="0" style="position:absolute" aria-hidden="true"><defs>
<symbol id="i-send" viewBox="0 0 24 24"><path d="M22 2 11 13"/><path d="M22 2 15 22l-4-9-9-4 20-7z"/></symbol>
<symbol id="i-truck" viewBox="0 0 24 24"><path d="M1 4h15v12H1z"/><path d="M16 9h4l3 3v4h-7z"/><circle cx="5.5" cy="18.5" r="2"/><circle cx="18.5" cy="18.5" r="2"/></symbol>
<symbol id="i-edit" viewBox="0 0 24 24"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/></symbol>
<symbol id="i-back" viewBox="0 0 24 24"><path d="m15 18-6-6 6-6"/></symbol>
<symbol id="i-check" viewBox="0 0 24 24"><path d="M20 6 9 17l-5-5"/></symbol>
<symbol id="i-search" viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/></symbol>
<symbol id="i-inbox" viewBox="0 0 24 24"><path d="M22 12h-6l-2 3h-4l-2-3H2"/><path d="M5.5 5.1 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.5-6.9A2 2 0 0 0 16.7 4H7.3a2 2 0 0 0-1.8 1.1z"/></symbol>
</defs></svg>`);
