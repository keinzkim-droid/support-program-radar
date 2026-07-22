"""classified.json → 정적 HTML.

디자인은 시우님이 만든 원본(지원사업 노트)의 시각 언어를 따른다.
색·폰트·카드 형태를 새로 만들지 않고 그대로 쓴다.

산출물은 docs/index.html 하나로 자족적이어야 한다(외부 JS 의존 없음).
"""

from __future__ import annotations

import html
import json
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).parent
IN = ROOT / "data" / "classified.json"
OUT = ROOT / "docs" / "index.html"

# 승인 목록을 GitHub 웹 편집기로 바로 여는 링크.
REPO_NEW = "https://github.com/keinzkim-droid/support-program-radar/new/main"

# 자동 실행 시각 안내. .github/workflows/daily.yml의 cron과 함께 고칠 것.
SCHEDULE_TEXT = "매일 오전 10시 · 오후 1시경"

# 소스 코드명 → 화면에 쓸 기관명.
# 새 소스를 collect.py에 추가하면 여기에도 한 줄 넣으면 된다.
SOURCE_LABEL = {
    "bizinfo": "기업마당",
    "kiria": "한국로봇산업진흥원",
    "nipa": "정보통신산업진흥원",
    "snip": "성남산업진흥원",
    "kstartup": "K-Startup",
}

TRACK_LABEL = {
    "A": ("직접 신청", "우리가 신청자"),
    "B": ("고객사 제안", "고객사가 신청자 · 우리는 공급기업"),
    "C": ("사무실·공간", "임대료·입주 지원"),
}

CSS = """
:root{
  --blue-50:#eef5ff;--blue-100:#d6e9ff;--blue-300:#7ab4ff;--blue-500:#3396ff;
  --blue-600:#1c7ef2;--blue-700:#1259bd;--green-500:#00c9a7;--amber-500:#ffb020;
  --n0:#fff;--n25:#fbfbfc;--n50:#f5f6f8;--n100:#eceef1;--n200:#dfe1e6;
  --n400:#a8adb8;--n600:#6e7280;--n800:#33353d;--n900:#171719;
  --bg:var(--n25);--surface:var(--n0);--text:var(--n900);--text2:var(--n600);
  --border:var(--n100);--accent:var(--blue-500);--accent-strong:var(--blue-700);
  --soft:var(--blue-50);
}
*{box-sizing:border-box}html,body{margin:0;padding:0}
body{background:var(--bg);color:var(--text);
  font-family:'Pretendard Variable',-apple-system,'Malgun Gothic','Noto Sans KR',sans-serif;
  -webkit-font-smoothing:antialiased}
.nav{position:sticky;top:0;z-index:20;background:rgba(255,255,255,.92);
  backdrop-filter:blur(10px);border-bottom:1px solid var(--border)}
.nav-inner{max-width:1160px;margin:0 auto;padding:16px 28px;display:flex;
  align-items:center;justify-content:space-between}
.brand{display:flex;align-items:center;gap:9px;font-weight:800;font-size:17px;
  letter-spacing:-.01em}
.brand .dot{width:9px;height:9px;border-radius:2px;background:var(--accent);
  transform:rotate(45deg)}
.nav-tag{font-size:12.5px;color:var(--text2);background:var(--n50);padding:6px 12px;
  border-radius:100px;border:1px solid var(--border)}
.hero{max-width:1160px;margin:0 auto;padding:56px 28px 32px}
.eyebrow{display:inline-flex;align-items:center;gap:8px;font-size:13px;font-weight:600;
  color:var(--accent-strong);background:var(--soft);padding:7px 13px;border-radius:100px;
  margin-bottom:20px}
h1{font-weight:800;font-size:clamp(28px,4.4vw,42px);line-height:1.28;
  letter-spacing:-.02em;margin:0 0 16px}
h1 .hl{color:var(--accent)}
.hero p{font-size:15.5px;line-height:1.7;color:var(--text2);max-width:640px;margin:0 0 30px}
.stat-row{display:flex;border-top:1px solid var(--border);padding-top:22px}
.stat{flex:1;padding-right:22px}
.stat+.stat{border-left:1px solid var(--border);padding-left:22px}
.stat-num{font-weight:800;font-size:26px;color:var(--accent-strong)}
.stat-label{font-size:12.5px;color:var(--text2);margin-top:4px}
.tabs{max-width:1160px;margin:0 auto;padding:0 28px;display:flex;gap:4px;
  border-bottom:1px solid var(--border)}
.tab-btn{display:flex;align-items:center;gap:8px;padding:14px 18px;font-weight:700;
  font-size:15px;color:var(--text2);background:none;border:none;
  border-bottom:2px solid transparent;cursor:pointer;font-family:inherit}
.tab-btn.active{color:var(--accent-strong);border-bottom-color:var(--accent)}
.tab-btn .count{font-size:11px;font-weight:700;background:var(--n100);color:var(--text2);
  padding:2px 7px;border-radius:100px}
.tab-btn.active .count{background:var(--soft);color:var(--accent-strong)}
.live-dot{width:6px;height:6px;border-radius:50%;background:#ff4d4f;
  box-shadow:0 0 0 3px rgba(255,77,79,.15)}
.tab-panel{display:none}.tab-panel.active{display:block}
.section-head{max-width:1160px;margin:40px auto 18px;padding:0 28px;display:flex;
  align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px}
.section-head h2{font-size:20px;font-weight:800;margin:0}
.section-sub{font-size:13px;color:var(--text2)}
.grid{max-width:1160px;margin:0 auto;padding:0 28px 8px;display:grid;
  grid-template-columns:repeat(2,1fr);gap:16px}
@media(max-width:840px){.grid{grid-template-columns:1fr}}
.card{background:var(--surface);border:1px solid var(--border);border-radius:16px;
  padding:20px;transition:box-shadow .18s,border-color .18s,transform .18s}
.card:hover{border-color:var(--blue-300);box-shadow:0 8px 24px rgba(51,150,255,.12);
  transform:translateY(-2px)}
.card-head{display:flex;align-items:flex-start;gap:12px;margin-bottom:14px}
.badge{flex:none;width:42px;height:42px;border-radius:12px;display:flex;
  align-items:center;justify-content:center;font-weight:800;font-size:14px;
  background:var(--soft);color:var(--accent-strong)}
.badge.b{background:#fff4e0;color:#a5670a}
.card-head-text{flex:1;min-width:0}
.card-kicker{font-size:11.5px;font-weight:700;color:var(--text2);margin-bottom:3px}
.card-title{font-size:15.5px;font-weight:700;line-height:1.4}
.card-title a{color:inherit;text-decoration:none}
.card-title a:hover{color:var(--accent-strong);text-decoration:underline}
.pill{flex:none;font-size:11px;font-weight:700;padding:5px 10px;border-radius:100px;
  white-space:nowrap}
.pill.open{color:#067a5c;background:#e3f9f2}
.pill.soon{color:#a5670a;background:#fff4e0}
.pill.always{color:#1259bd;background:#eef5ff}
.pill.closed{color:#6e7280;background:#f5f6f8}
.chip-row{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:12px}
.chip{font-size:11.5px;font-weight:600;padding:5px 9px;border-radius:8px;
  background:var(--n50);color:var(--n800);border:1px solid var(--border)}
.chip.track{background:var(--soft);color:var(--accent-strong);border-color:transparent}
.chip.cond{background:#fff4e0;color:#a5670a;border-color:#f3d9a8}
.cond-table{border-top:1px solid var(--border);padding-top:12px}
.cond-row{display:flex;justify-content:space-between;gap:12px;font-size:12.5px;padding:4px 0}
.cond-k{color:var(--text2);flex:none;width:72px}
.cond-v{color:var(--text);font-weight:600;text-align:right}
.why{font-size:11.5px;color:var(--text2);margin-top:10px;line-height:1.6}
.acts{display:flex;gap:8px;margin-top:14px;padding-top:12px;
  border-top:1px dashed var(--border)}
.btn{font-family:inherit;font-size:13px;font-weight:700;padding:9px 16px;
  border-radius:9px;border:1px solid var(--blue-300);cursor:pointer;
  background:var(--accent);color:#fff}
.btn.sm{font-size:12.5px;padding:7px 13px}
.btn.ghost{background:var(--surface);color:var(--accent-strong)}
.btn:hover{filter:brightness(1.05)}
/* 클릭 후 무슨 일이 일어났는지 알려주는 알림 */
.toast{position:fixed;left:50%;bottom:24px;transform:translateX(-50%) translateY(140%);
  max-width:min(560px,92vw);z-index:60;background:var(--n900);color:#fff;
  padding:16px 20px;border-radius:14px;font-size:13.5px;line-height:1.65;
  box-shadow:0 12px 34px rgba(0,0,0,.28);cursor:pointer;
  transition:transform .28s ease,opacity .28s ease;opacity:0}
.toast.on{transform:translateX(-50%) translateY(0);opacity:1}
.toast b{color:var(--blue-300)}
.toast .t-note{display:block;margin-top:8px;padding-top:8px;
  border-top:1px solid rgba(255,255,255,.18);color:var(--n400);font-size:12.5px}
/* 새 데이터가 올라왔을 때 뜨는 알림 */
.fresh{position:fixed;right:20px;bottom:20px;z-index:55;display:none;
  align-items:center;gap:12px;background:var(--accent);color:#fff;
  padding:12px 14px 12px 18px;border-radius:12px;font-size:13.5px;font-weight:600;
  box-shadow:0 10px 28px rgba(51,150,255,.35)}
.fresh.on{display:flex}
.fresh .btn{background:#fff;color:var(--accent-strong);border-color:#fff}
@media(max-width:600px){.fresh{left:16px;right:16px;justify-content:space-between}}
/* 한눈에 비교 */
.table-wrap{max-width:1160px;margin:0 auto 32px;padding:0 28px;overflow-x:auto}
.table-wrap table{width:100%;min-width:900px;border-collapse:separate;
  border-spacing:0;background:var(--surface);border:1px solid var(--border);
  border-radius:14px;overflow:hidden;font-size:13px}
.table-wrap thead th{text-align:left;font-weight:700;font-size:12.5px;
  color:var(--text2);background:var(--n50);padding:13px 15px;
  border-bottom:1px solid var(--border);white-space:nowrap;
  cursor:pointer;user-select:none}
.table-wrap thead th:hover{background:var(--soft);color:var(--accent-strong)}
.table-wrap thead th[data-dir]{color:var(--accent-strong)}
.arw{margin-left:6px;font-size:10px;opacity:.55}
.table-wrap thead th[data-dir] .arw{opacity:1}
.table-wrap tbody td{padding:13px 15px;border-bottom:1px solid var(--border);
  color:var(--text2);vertical-align:top;line-height:1.55}
.table-wrap tbody tr:last-child td{border-bottom:none}
.table-wrap tbody tr:hover td{background:var(--soft)}
.table-wrap td.name{color:var(--text);font-weight:700}
.table-wrap td.name a{color:inherit;text-decoration:none}
.table-wrap td.name a:hover{text-decoration:underline}
.empty{max-width:1160px;margin:0 auto;padding:40px 28px;color:var(--text2);
  font-size:14px;text-align:center;background:var(--surface);border:1px dashed var(--border);
  border-radius:16px}
.notice{max-width:1160px;margin:0 auto 20px;padding:16px 20px;background:var(--soft);
  border:1px solid var(--blue-300);border-radius:12px;font-size:13px;line-height:1.7;
  color:var(--n800)}
.notice code{background:rgba(255,255,255,.7);padding:2px 6px;border-radius:4px;
  font-size:12px}
.wrap{max-width:1160px;margin:0 auto;padding:0 28px}
footer{max-width:1160px;margin:0 auto;padding:32px 28px 56px;
  border-top:1px solid var(--border);display:flex;justify-content:space-between;
  flex-wrap:wrap;gap:10px;font-size:12px;color:var(--n400);margin-top:40px}
@media(max-width:600px){
  .hero,.section-head,.grid,.wrap,footer,.tabs{padding-left:18px;padding-right:18px}
  .stat-row{flex-wrap:wrap;gap:14px}
  .stat{flex:1 1 40%;padding-right:0}
  .stat+.stat{border-left:none;padding-left:0}
}
"""

JS = """
function switchTab(name, el){
  document.querySelectorAll('.tab-panel').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(b=>b.classList.remove('active'));
  document.getElementById('tab-'+name).classList.add('active');
  el.classList.add('active');
}

var LABEL = { approved: '관련 사업', rejected: '제외 목록', pending: '새로 찾은 공고' };

// 정적 페이지라 서버에 저장할 수 없다. 대신 결정 하나를 작은 파일 하나로 만들어
// GitHub 새 파일 화면을 '내용까지 채운 채로' 연다. 사용자는 커밋만 누르면 된다.
function decide(key, title, decision){
  var now = new Date();
  var ts = now.toISOString().replace(/[-:T]/g,'').slice(0,14);   // yyyymmddhhmmss
  var safe = key.replace(/[^A-Za-z0-9]/g,'-');
  var name = 'config/decisions/' + ts + '-' + safe + '.yml';
  var body = 'key: ' + key + '\\n'
           + 'decision: ' + decision + '\\n'
           + 'title: "' + title.replace(/"/g,"'") + '"\\n'
           + 'decided_at: ' + now.toISOString().slice(0,10) + '\\n';
  var url = REPO_NEW + '?filename=' + encodeURIComponent(name)
          + '&value=' + encodeURIComponent(body);
  window.open(url, '_blank', 'noopener');
  toast(title, decision);
}

function toast(title, decision){
  var el = document.getElementById('toast');
  el.innerHTML = '<b>' + LABEL[decision] + '</b>(으)로 이동 요청했습니다.<br>'
    + '새로 열린 GitHub 화면에서 초록색 <b>Commit changes</b> 버튼을 누르면 접수됩니다.<br>'
    + '<span class="t-note">화면에는 <b>다음 자동 실행(' + SCHEDULE_TXT + ')</b> 때 '
    + '반영됩니다. 지금 바로 보려면 Actions에서 수동 실행하세요.</span>';
  el.classList.add('on');
  clearTimeout(window._tt);
  window._tt = setTimeout(function(){ el.classList.remove('on'); }, 12000);
}

function hideToast(){ document.getElementById('toast').classList.remove('on'); }

// 브라우저가 이 페이지를 캐시해두기 때문에, 갱신이 끝나도 옛 화면이 보일 수 있다.
// 갱신 시각만 담은 작은 파일을 캐시 우회로 받아 비교하고, 다르면 알려준다.
// (사용자가 매번 강제 새로고침을 눌러야 하는 상황을 없애려는 것)
function checkFresh(){
  fetch('version.json?t=' + Date.now(), { cache: 'no-store' })
    .then(function(r){ return r.ok ? r.json() : null; })
    .then(function(v){
      if(v && BUILD_ID && v.build !== BUILD_ID){
        document.getElementById('fresh').classList.add('on');
      }
    })
    .catch(function(){ /* 네트워크 문제면 조용히 넘어간다 */ });
}

function reloadNow(){ location.reload(); }

window.addEventListener('load', function(){
  checkFresh();
  setInterval(checkFresh, 5 * 60 * 1000);   // 5분마다 확인
});

// 비교표 정렬. 같은 칸을 다시 누르면 방향이 뒤집힌다.
function sortTable(th, col){
  var table = th.closest('table');
  var body = table.tBodies[0];
  var asc = th.dataset.dir !== 'asc';

  [...table.querySelectorAll('th')].forEach(function(h){
    if(h !== th){ delete h.dataset.dir; h.querySelector('.arw').textContent = '↕'; }
  });
  th.dataset.dir = asc ? 'asc' : 'desc';
  th.querySelector('.arw').textContent = asc ? '▲' : '▼';

  var val = function(tr){
    var td = tr.cells[col];
    // 표시값과 정렬값이 다른 칸은 data-v를 쓴다
    return (td.dataset.v !== undefined ? td.dataset.v : td.innerText).trim();
  };
  [...body.rows]
    .sort(function(a, b){
      var x = val(a), y = val(b);
      return asc ? x.localeCompare(y, 'ko') : y.localeCompare(x, 'ko');
    })
    .forEach(function(tr){ body.appendChild(tr); });
}
"""


def esc(s) -> str:
    return html.escape(str(s or ""))


def status_pill(status: str) -> str:
    cls = {"접수중": "open", "마감임박": "soon", "상시": "always", "마감": "closed"}
    return f'<span class="pill {cls.get(status, "closed")}">{esc(status)}</span>'


def deadline_note(rec: dict, today: date) -> str:
    """마감까지 남은 일수. 급한 것을 눈에 띄게 하는 게 목적이다."""
    end = rec.get("apply_end")
    if not end:
        return rec.get("apply_raw") or "상시"
    left = (date.fromisoformat(end) - today).days
    if left < 0:
        return f"{end} (마감)"
    if left == 0:
        return f"{end} (오늘 마감)"
    return f"{end} (D-{left})"


def card_html(rec: dict, idx: int, today: date,
              selectable: str | bool = False) -> str:
    track = rec["track"]
    kicker, _ = TRACK_LABEL.get(track, ("", ""))
    # 카드 상태에 따라 이동 버튼을 붙인다. 클릭하면 GitHub에 결정 파일을
    # 내용까지 채운 채로 열어주고, 사용자는 커밋 버튼만 누르면 된다.
    key, title = esc(rec["key"]), esc(rec["title"])
    if selectable == "new":          # 새로 찾은 공고
        pick = (f'<div class="acts">'
                f'<button class="btn sm" onclick="decide(\'{key}\',\'{title}\',\'approved\')">'
                f'관련 사업으로</button>'
                f'<button class="btn sm ghost" onclick="decide(\'{key}\',\'{title}\',\'rejected\')">'
                f'제외</button></div>')
    elif selectable:                 # 이미 분류된 공고 → 되돌리기
        pick = (f'<div class="acts">'
                f'<button class="btn sm ghost" onclick="decide(\'{key}\',\'{title}\',\'pending\')">'
                f'되돌리기</button></div>')
    else:
        pick = ""
    # 제외된 공고는 트랙이 없다(track='-'). 빈 칩 대신 상태를 보여준다.
    chips = ([f'<span class="chip track">{esc(kicker)}</span>'] if kicker
             else ['<span class="chip">제외됨</span>'])
    if rec.get("region"):
        chips.append(f'<span class="chip">{esc(rec["region"])}</span>')
    if rec.get("category"):
        chips.append(f'<span class="chip">{esc(rec["category"])}</span>')
    if rec.get("conditional"):
        chips.append(f'<span class="chip cond">조건부 · {esc(rec["conditional"])}</span>')

    reasons = " · ".join(rec.get("reasons") or [])
    return f"""
  <div class="card">
    <div class="card-head">
      <div class="badge {'b' if track == 'B' else ''}">{idx:02d}</div>
      <div class="card-head-text">
        <div class="card-kicker">{esc(rec.get('agency'))}</div>
        <div class="card-title"><a href="{esc(rec['url'])}" target="_blank"
          rel="noopener">{esc(rec['title'])}</a></div>
      </div>
      {status_pill(rec['status'])}
    </div>
    <div class="chip-row">{''.join(chips)}</div>
    <div class="cond-table">
      <div class="cond-row"><span class="cond-k">접수기간</span>
        <span class="cond-v">{esc(deadline_note(rec, today))}</span></div>
      <div class="cond-row"><span class="cond-k">수행기관</span>
        <span class="cond-v">{esc(rec.get('exec_agency') or '-')}</span></div>
      <div class="cond-row"><span class="cond-k">출처</span>
        <span class="cond-v">{esc(rec.get('source'))}</span></div>
    </div>
    <div class="why">판정 근거 · {esc(reasons)}</div>
    {pick}
  </div>"""


def grid(recs: list[dict], today: date, empty_msg: str,
         selectable: str | bool = False) -> str:
    if not recs:
        return f'<div class="wrap"><div class="empty">{esc(empty_msg)}</div></div>'
    cards = "".join(card_html(r, i + 1, today, selectable)
                    for i, r in enumerate(recs))
    return f'<div class="grid">{cards}</div>'


def compare_table(recs: list[dict], today: date) -> str:
    """한눈에 비교. 카드를 하나씩 읽지 않고 전체를 훑을 때 쓴다."""
    if not recs:
        return ""
    # 정렬용 값. 화면에 보이는 문자열과 정렬 기준이 다른 칸이 있다.
    # 접수기간은 'D-12' 같은 표시라 그대로 정렬하면 엉키므로 날짜를 따로 넘긴다.
    status_order = {"마감임박": "1", "접수중": "2", "상시": "3", "마감": "4"}
    rows = []
    for r in recs:
        track = "직접 신청" if r["track"] == "A" else "고객사 제안"
        cond = ' <span class="chip cond">조건부</span>' if r.get("conditional") else ""
        # 마감일이 없는 '상시'는 맨 뒤로 보낸다
        end_key = r.get("apply_end") or "9999-12-31"
        rows.append(f"""
      <tr>
        <td>{esc(track)}</td>
        <td class="name"><a href="{esc(r['url'])}" target="_blank"
            rel="noopener">{esc(r['title'])}</a>{cond}</td>
        <td>{esc(r.get('agency') or '-')}</td>
        <td>{esc(r.get('region') or '전국')}</td>
        <td data-v="{esc(end_key)}">{esc(deadline_note(r, today))}</td>
        <td data-v="{status_order.get(r['status'], '9')}">{status_pill(r['status'])}</td>
      </tr>""")

    heads = ["구분", "사업명", "소관기관", "지역", "접수기간", "상태"]
    th = "".join(
        f'<th onclick="sortTable(this,{i})">{h}<span class="arw">↕</span></th>'
        for i, h in enumerate(heads))
    return f"""
<div class="section-head"><h2>한눈에 비교</h2>
  <div class="section-sub">{len(recs)}건 전체 · 제목을 누르면 정렬됩니다</div></div>
<div class="table-wrap"><table id="cmp">
  <thead><tr>{th}</tr></thead>
  <tbody>{''.join(rows)}</tbody>
</table></div>"""


def build(data: dict, build_id: str = "") -> str:
    today = date.fromisoformat(data["generated_at"])
    cards = data["cards"]
    cands = data["candidates"]
    expired = data.get("expired", [])
    excluded = data.get("excluded", [])
    archive_days = data.get("archive_after_days", 30)
    st = data["stats"]
    # 모르는 소스가 생겨도 코드명이라도 나오게 한다(빈칸보다 낫다).
    src_names = [SOURCE_LABEL.get(s, s) for s in data.get("sources", [])]
    src_line = " + ".join(src_names) if src_names else "자동 수집"
    # 사무실·공간은 성격이 달라 별도 탭으로 뺀다(로봇 사업과 함께 두면 묻힌다).
    office = [r for r in cards if r["track"] == "C"]
    cards = [r for r in cards if r["track"] != "C"]

    open_now = [r for r in cards + cands if r["status"] in ("접수중", "마감임박")]
    open_now.sort(key=lambda r: r.get("apply_end") or "9999")

    track_a = len([r for r in cands + cards if r["track"] == "A"])
    track_b = len([r for r in cands + cards if r["track"] == "B"])

    return f"""<!DOCTYPE html>
<html lang="ko"><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<!-- 사내용 페이지. 검색엔진 색인에서 제외한다.
     구글·네이버 모두 이 지시를 따른다. 다만 링크를 아는 사람은 볼 수 있으므로
     비공개가 아니라 '검색에 안 잡히는 공개'임에 유의. -->
<meta name="robots" content="noindex, nofollow, noarchive">
<title>지원사업 레이더 · 자동 수집</title>
<style>{CSS}</style>
</head><body>

<nav class="nav"><div class="nav-inner">
  <div class="brand"><span class="dot"></span>지원사업 레이더</div>
  <div class="nav-tag">{esc(data['generated_at'])} 자동 갱신</div>
</div></nav>

<header class="hero">
  <div class="eyebrow">● 자동 수집 · {esc(src_line)}</div>
  <h1>지금 접수 중인 사업<br><span class="hl">{len(open_now)}건</span>을 찾았어요</h1>
  <p>매일 자동으로 공고를 수집해 회사 조건에 맞는 것만 골라냅니다.
     직접 신청하는 사업과 고객사에 제안할 사업을 나눠서 보여줍니다.</p>
  <div class="stat-row">
    <div class="stat"><div class="stat-num">{st['collected']}</div>
      <div class="stat-label">오늘 수집한 공고</div></div>
    <div class="stat"><div class="stat-num">{track_a}</div>
      <div class="stat-label">직접 신청 대상</div></div>
    <div class="stat"><div class="stat-num">{track_b}</div>
      <div class="stat-label">고객사 제안용</div></div>
    <div class="stat"><div class="stat-num">{len(cands)}</div>
      <div class="stat-label">새로 찾은 공고</div></div>
  </div>
</header>

<div class="tabs">
  <button class="tab-btn active" onclick="switchTab('open', this)">
    접수 중 <span class="count">{len(open_now)}</span></button>
  <button class="tab-btn" onclick="switchTab('cards', this)">
    관련 사업 <span class="count">{len(cards)}</span></button>
  <button class="tab-btn" onclick="switchTab('office', this)">
    사무실·공간 <span class="count">{len(office)}</span></button>
  <button class="tab-btn" onclick="switchTab('new', this)">
    <span class="live-dot"></span>새로 찾은 공고 <span class="count">{len(cands)}</span></button>
  <button class="tab-btn" onclick="switchTab('expired', this)">
    마감 <span class="count">{len(expired)}</span></button>
  <button class="tab-btn" onclick="switchTab('excluded', this)">
    제외 <span class="count">{len(excluded)}</span></button>
</div>

<div class="tab-panel" id="tab-cards">
  <div class="section-head"><h2>관련 사업</h2>
    <div class="section-sub">검토를 마친 공고 · 마감된 것은 '마감' 탭으로 이동합니다</div></div>
  {grid(cards, today, "아직 목록에 올린 공고가 없습니다. '새로 찾은 공고' 탭을 확인해주세요.",
        selectable=True)}
  {compare_table(cards, today)}
</div>

<div class="tab-panel active" id="tab-open">
  <div class="section-head"><h2>접수 중</h2>
    <div class="section-sub">마감이 가까운 순서</div></div>
  {grid(open_now, today, "현재 접수 중인 공고가 없습니다.")}
</div>

<div class="tab-panel" id="tab-office">
  <div class="section-head"><h2>사무실·공간</h2>
    <div class="section-sub">임대료 지원 · 입주기업 모집 (서울 · 경기)</div></div>
  {grid(office, today, "해당 공고가 없습니다.", selectable=True)}
  {compare_table(office, today)}
</div>

<div class="tab-panel" id="tab-new">
  <div class="section-head"><h2>새로 찾은 공고</h2>
    <div class="section-sub">자동 발견 · 아직 검토 전</div></div>
  <div class="wrap"><div class="notice">
    <b>기계가 자동으로 찾아낸 공고입니다.</b> 관련 없는 건이 섞여 있을 수 있어,
    사람이 확인하기 전까지는 '관련 사업'에 올리지 않습니다.<br>
    목록에 추가하려면 저장소의 <code>config/curated.yaml</code>에서
    <code>approved:</code> 아래에 카드 키를 넣고, 제외하려면
    카드의 <b>관련 사업으로</b> 또는 <b>제외</b> 버튼을 누르면 GitHub 화면이 열립니다.
    거기서 초록색 <b>Commit changes</b> 버튼만 누르면 접수됩니다.<br>
    반영은 <b>다음 자동 실행({SCHEDULE_TEXT})</b> 때 이루어집니다.
  </div></div>
  {grid(cands, today, "새로 발견된 공고가 없습니다.", selectable="new")}
</div>

<div class="tab-panel" id="tab-expired">
  <div class="section-head"><h2>마감</h2>
    <div class="section-sub">최근 마감된 순서</div></div>
  <div class="wrap"><div class="notice">
    <b>접수가 끝난 공고입니다.</b> 놓친 공고를 확인하거나 다음 회차를 준비할 때 참고하세요.<br>
    마감일로부터 <b>{archive_days}일</b>이 지나면 이 목록에서 자동으로 사라집니다.
    (승인 기록 자체는 <code>config/curated.yaml</code>에 남아 있어 이력은 보존됩니다.)
  </div></div>
  {grid(expired, today, "마감된 공고가 없습니다.", selectable=True)}
</div>

<div class="tab-panel" id="tab-excluded">
  <div class="section-head"><h2>제외</h2>
    <div class="section-sub">관련 없다고 판단해 목록에서 뺀 공고</div></div>
  <div class="wrap"><div class="notice">
    <b>제외 처리한 공고입니다.</b> 잘못 눌렀다면 <b>되돌리기</b>로 되살릴 수 있습니다.<br>
    제외한 날로부터 <b>{archive_days}일</b>이 지나면 이 목록에서도 사라집니다.
    (그 뒤에도 조건에 맞으면 '새로 찾은 공고'로 다시 올라올 수 있습니다.)
  </div></div>
  {grid(excluded, today, "제외한 공고가 없습니다.", selectable=True)}
</div>

<footer>
  <span>자동 수집 · {esc(' · '.join(src_names))}</span>
  <span>마지막 갱신 {esc(datetime.now().strftime('%Y-%m-%d %H:%M'))}</span>
</footer>

<div class="toast" id="toast" onclick="hideToast()"></div>

<div class="fresh" id="fresh">
  <span>새로운 공고 정보가 있습니다.</span>
  <button class="btn sm" onclick="reloadNow()">새로고침</button>
</div>

<script>
var REPO_NEW = {json.dumps(REPO_NEW)};
var SCHEDULE_TXT = {json.dumps(SCHEDULE_TEXT)};
var BUILD_ID = {json.dumps(build_id)};
</script>
<script>{JS}</script>
</body></html>
"""


def main() -> int:
    data = json.loads(IN.read_text(encoding="utf-8"))
    OUT.parent.mkdir(parents=True, exist_ok=True)

    # 페이지가 '내가 보고 있는 게 최신인가'를 스스로 확인할 수 있도록
    # 빌드 시각만 담은 작은 파일을 따로 둔다.
    # GitHub Pages는 HTML을 10분간 캐시하라고 브라우저에 알리고 그 헤더는
    # 우리가 못 바꾼다. 대신 이 파일을 캐시 우회로 받아 비교한다.
    build_id = datetime.now().strftime("%Y-%m-%d %H:%M")
    (OUT.parent / "version.json").write_text(
        json.dumps({"build": build_id, "generated_at": data["generated_at"]},
                   ensure_ascii=False),
        encoding="utf-8",
    )

    OUT.write_text(build(data, build_id), encoding="utf-8")
    print(f"생성 {OUT.relative_to(ROOT)} "
          f"(승인 {len(data['cards'])} / 후보 {len(data['candidates'])})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
