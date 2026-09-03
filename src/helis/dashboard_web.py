from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from helis.dashboard import DashboardSnapshotBuilder

DASHBOARD_HTML = """<!doctype html>
<html lang="pl">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>HELIS — kokpit właściciela</title>
  <style>
    :root{--bg:#07100d;--panel:#101c17;--panel2:#15251e;--line:#294035;--ink:#edf7f1;
      --muted:#9ab4a7;--green:#69ed9d;--amber:#ffc76b;--red:#ff7b79;--blue:#7ec9ff}
    *{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 80% 0,#153528 0,
      var(--bg) 38%);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
    main{max-width:1240px;margin:auto;padding:32px 22px 70px}header{display:flex;gap:24px;
      align-items:flex-end;justify-content:space-between;margin-bottom:24px}.brand{letter-spacing:.2em;
      font-size:12px;color:var(--green);font-weight:800}.brand strong{display:block;letter-spacing:-.03em;
      font-size:36px;color:var(--ink)}h1,h2,h3,p{margin-top:0}.muted{color:var(--muted)}
    .status{max-width:650px;font-size:18px}.grid{display:grid;grid-template-columns:repeat(6,1fr);
      gap:12px}.metric,.panel,.venture{background:linear-gradient(145deg,var(--panel2),var(--panel));
      border:1px solid var(--line);border-radius:16px;box-shadow:0 14px 35px #0004}.metric{padding:16px}
    .metric b{display:block;font-size:28px}.metric span{color:var(--muted);font-size:12px}
    .columns{display:grid;grid-template-columns:2fr 1fr;gap:18px;margin-top:18px}.panel{padding:20px}
    .section{margin-top:28px}.section-head{display:flex;justify-content:space-between;align-items:center}
    .venture{padding:20px;margin:12px 0}.venture-head{display:flex;gap:14px;justify-content:space-between}
    .venture h3{margin:0;font-size:20px}.pill{display:inline-block;border:1px solid var(--line);
      padding:3px 9px;border-radius:99px;color:var(--muted);font-size:12px;margin:5px 5px 0 0}
    .score{font-size:28px;font-weight:800;color:var(--green);white-space:nowrap}.details{display:grid;
      grid-template-columns:1fr 1fr;gap:12px;margin-top:15px}.box{background:#07100d80;padding:13px;
      border-radius:11px}.box small{color:var(--muted);display:block;margin-bottom:4px}ul{padding-left:19px}
    .activity{list-style:none;padding:0}.activity li{border-left:2px solid var(--line);padding:0 0 14px 13px}
    .approval{border-left:3px solid var(--amber);padding:10px 12px;background:#ffc76b0c;margin:8px 0}
    .box.failed{border:1px solid var(--red);background:#ff7b790a}.box.failed div{color:var(--red)}
    .empty{padding:34px;text-align:center;border:1px dashed var(--line);border-radius:14px;color:var(--muted)}
    button{background:transparent;color:var(--ink);border:1px solid var(--line);border-radius:9px;
      padding:8px 12px;cursor:pointer}button:hover{border-color:var(--green)}code{color:var(--blue)}
    @media(max-width:900px){.grid{grid-template-columns:repeat(3,1fr)}.columns{grid-template-columns:1fr}}
    @media(max-width:560px){main{padding:20px 14px}.grid{grid-template-columns:1fr 1fr}header{display:block}
      .details{grid-template-columns:1fr}.score{font-size:20px}}
  </style>
</head>
<body><main>
  <header><div><div class="brand">AUTONOMICZNY SILNIK PRZEDSIĘWZIĘĆ<strong>HELIS</strong></div>
    <p class="muted">Kokpit właściciela · tylko do odczytu</p></div>
    <div><button id="refresh">Odśwież</button><p id="updated" class="muted"></p></div></header>
  <p id="message" class="status">Ładowanie stanu…</p>
  <section id="metrics" class="grid"></section>
  <div class="columns"><section class="panel"><h2>Ostatnie przebiegi</h2><div id="loops"></div></section>
    <aside class="panel"><h2>Decyzje dla Ciebie</h2><div id="approvals"></div></aside></div>
  <section class="section"><div class="section-head"><h2>Przedsięwzięcia i pomysły</h2>
    <span id="ventureCount" class="muted"></span></div><div id="ventures"></div></section>
  <div class="columns"><section class="panel"><h2>Ostatnia aktywność</h2><ul id="activity" class="activity"></ul></section>
    <aside class="panel"><h2>Budowane pliki</h2><div id="workspace"></div></aside></div>
</main><script>
const el=id=>document.getElementById(id); const txt=(tag,value,cls)=>{const n=document.createElement(tag);
  n.textContent=value??"—";if(cls)n.className=cls;return n};
const labels={observations:"Sygnały rynkowe",opportunities:"Pomysły",active_ventures:"Aktywne",
  builds:"Przebiegi budowy",leads:"Potencjalni klienci",pending_approvals:"Czekające decyzje"};
const stage={discovered:"odkryty",evaluated:"oceniony",validating:"walidacja",validated:"zwalidowany",
  building:"budowa",ready_preview:"MVP gotowe",launched:"uruchomiony",measuring:"pomiar",
  scaling:"skalowanie",pivoted:"zmieniony",paused:"wstrzymany",killed:"odrzucony"};
function clear(node){while(node.firstChild)node.removeChild(node.firstChild)}
function empty(node,message){node.appendChild(txt("div",message,"empty"))}
function render(data){el("message").textContent=data.message;el("updated").textContent="Stan: "+new Date(data.generated_at).toLocaleString();
  clear(el("metrics"));Object.entries(data.summary).forEach(([key,val])=>{const card=txt("div","","metric");card.append(txt("b",val));
    card.append(txt("span",labels[key]||key));el("metrics").append(card)});
  clear(el("loops"));[["Odkrywanie",data.discovery],["Realizacja",data.scheduler]].forEach(([name,item])=>{const box=txt("div","","box");
    if(item?.disposition==="failed")box.classList.add("failed");
    box.append(txt("small",name));box.append(txt("div",item?`${item.disposition||"—"} · ${item.reason||""}`:"Brak zarejestrowanego przebiegu"));
    if(item?.attempted_at)box.append(txt("small",new Date(item.attempted_at).toLocaleString()));el("loops").append(box)});
  clear(el("approvals"));if(!data.approvals.length)empty(el("approvals"),"Nic nie czeka na Twoją zgodę.");
  data.approvals.forEach(item=>{const n=txt("div","","approval");n.append(txt("b",item.kind));n.append(txt("div",`Run: ${item.run_id}`));el("approvals").append(n)});
  clear(el("ventures"));el("ventureCount").textContent=`${data.ventures.length} łącznie`;if(!data.ventures.length)
    empty(el("ventures"),"HELIS jeszcze nie wybrał przedsięwzięcia. Nadal obserwuje rynek.");
  data.ventures.forEach(v=>{const card=txt("article","","venture"),head=txt("div","","venture-head"),left=txt("div","");
    left.append(txt("h3",v.title));left.append(txt("span",stage[v.stage]||v.stage,"pill"));left.append(txt("span",v.customer,"pill"));
    head.append(left);head.append(txt("div",v.score===null?"bez oceny":`${Number(v.score).toFixed(1)}/100`,"score"));card.append(head);
    card.append(txt("p",v.problem,"muted"));const details=txt("div","","details");
    [["Proponowana wartość",v.value],["Model zarabiania",v.business_model.revenue_model||"Jeszcze nieustalony"],
      ["Walidacja",`${v.validation.experiments} eksperymentów · ${v.validation.latest_status||"brak przebiegu"}`],
      ["Budowa",`${v.build.runs} przebiegów · ${v.build.latest_status||"jeszcze nic nie buduje"}`],
      ["Sprzedaż",`${v.gtm.leads} leadów · ${v.gtm.outreach_runs} kontaktów`],
      ["Dlaczego",(v.rationale||[]).join(" · ")||"Brak oceny"]].forEach(([k,val])=>{const b=txt("div","","box");b.append(txt("small",k));b.append(txt("div",val));details.append(b)});
    card.append(details);el("ventures").append(card)});
  clear(el("activity"));if(!data.activity.length)empty(el("activity"),"Brak zdarzeń.");data.activity.forEach(a=>{const li=txt("li","");
    li.append(txt("b",a.event_type));li.append(txt("div",new Date(a.created_at).toLocaleString(),"muted"));el("activity").append(li)});
  clear(el("workspace"));if(!data.workspace.length)empty(el("workspace"),"Nie zbudowano jeszcze plików.");data.workspace.slice(0,30).forEach(f=>{const b=txt("div","","box");
    b.append(txt("code",f.path));b.append(txt("small",`${f.size_bytes} B`));el("workspace").append(b)});
}
async function load(){try{const response=await fetch("/api/snapshot",{cache:"no-store"});if(!response.ok)throw new Error(`HTTP ${response.status}`);
  render(await response.json())}catch(error){el("message").textContent="Nie można odczytać stanu: "+error}}
el("refresh").addEventListener("click",load);load();setInterval(load,30000);
</script></body></html>"""


def make_handler(
    db: str | Path,
    workspace_root: str | Path,
) -> type[BaseHTTPRequestHandler]:
    builder = DashboardSnapshotBuilder(db, workspace_root)

    class DashboardHandler(BaseHTTPRequestHandler):
        server_version = "HELISDashboard/1"

        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0]
            if path == "/":
                self._send(DASHBOARD_HTML.encode("utf-8"), "text/html; charset=utf-8")
                return
            if path == "/api/snapshot":
                payload = json.dumps(
                    builder.build(), ensure_ascii=False, separators=(",", ":")
                ).encode("utf-8")
                self._send(payload, "application/json; charset=utf-8")
                return
            self.send_error(HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:
            self.send_error(HTTPStatus.METHOD_NOT_ALLOWED, "Dashboard is read-only")

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _send(self, body: bytes, content_type: str) -> None:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; img-src 'none'; object-src 'none'; frame-ancestors 'none'")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(body)

    return DashboardHandler


def dashboard_server(
    db: str | Path,
    workspace_root: str | Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> ThreadingHTTPServer:
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("dashboard may bind only to a loopback address")
    return ThreadingHTTPServer((host, port), make_handler(db, workspace_root))
