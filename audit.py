"""소스 파싱 감사 — 목록 행수 vs 파싱 성공수 대조.

각 소스의 목록 페이지를 받아, 데이터 행 수와 실제로 파싱에 성공한 수를
대조한다. 둘이 벌어지면 조용히 버려지는 공고가 있다는 뜻이다.

지난번 SNIP 재공고(<tr> 마크업 차이)와 NIPA curPage 버그를 이 방식으로
잡았다. 파서가 0건이 아니라 '일부'를 놓치면 CollectError가 안 나서
정상처럼 보인다 — 그 구멍을 드러내는 것이 이 감사의 목적이다.

버려진 행은 이유와 제목을 함께 찍는다. 진짜 공고가 아니라 헤더·안내행이면
정상 누락이고, 제목 있는 공고가 버려졌으면 파싱 버그다.

    python audit.py            # 소스별 1페이지 감사
    python audit.py --pages    # 다중 페이지 소스는 끝까지 훑어 페이지 커버리지도 본다
"""

from __future__ import annotations

import re
import sys

from bs4 import BeautifulSoup

import collect as C


def _rows_of(soup, table_picker):
    table = table_picker(soup)
    if table is None:
        return None, []
    body = table.find("tbody") or table
    return table, body.find_all("tr")


def _text_of(tr, limit=50):
    t = " ".join(tr.get_text(" ", strip=True).split())
    return t[:limit]


def audit_bizinfo():
    """기업마당 최신 목록 1페이지."""
    soup = BeautifulSoup(
        C.fetch(C.BIZINFO_URL, dict(C.BIZINFO_FORM, keyword="", cpage="1")),
        C.PARSER,
    )
    picker = lambda s: next(
        (t for t in s.find_all("table")
         if t.find("tbody") and t.find("a", href=lambda h: h and "pblancId" in h)),
        None,
    )
    table, rows = _rows_of(soup, picker)
    if table is None:
        return "bizinfo", None, [], ["표를 못 찾음 (사이트 개편?)"]

    parsed, skipped = 0, []
    for tr in rows:
        tds = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
        a = tr.find("a", href=lambda h: h and "pblancId" in h)
        if not a or len(tds) < 7:
            # 링크가 있는데 버려지면 수상하다.
            if a or _text_of(tr):
                skipped.append((f"td={len(tds)} link={'Y' if a else 'N'}", _text_of(tr)))
            continue
        if not re.search(r"pblancId=([A-Z_0-9]+)", a["href"]):
            skipped.append(("pblancId 미매칭", _text_of(tr)))
            continue
        parsed += 1
    return "bizinfo", len(rows), parsed, skipped


def audit_kiria():
    soup = BeautifulSoup(C.fetch("https://www.kiria.org/portal/info/portalInfoBusinessList.do"), C.PARSER)
    table, rows = _rows_of(soup, lambda s: next((t for t in s.find_all("table") if t.find("tbody")), None))
    if table is None:
        return "kiria", None, [], ["표를 못 찾음"]
    parsed, skipped = 0, []
    for tr in rows:
        tds = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
        a = tr.find("a")
        if not a or len(tds) < 7:
            if a or _text_of(tr):
                skipped.append((f"td={len(tds)} link={'Y' if a else 'N'}", _text_of(tr)))
            continue
        if not re.search(r"(IBUS_[0-9]+)", a.get("href", "") or ""):
            skipped.append(("IBUS 코드 미매칭", _text_of(tr)))
            continue
        parsed += 1
    return "kiria", len(rows), parsed, skipped


def audit_nipa(pages=1):
    all_rows, parsed, skipped = 0, 0, []
    for page in range(1, pages + 1):
        soup = BeautifulSoup(C.fetch(C.NIPA_URL, {"curPage": str(page)}), C.PARSER)
        table = soup.find("table")
        if table is None:
            break
        rows = (table.find("tbody") or table).find_all("tr")
        if not rows:
            break
        all_rows += len(rows)
        for tr in rows:
            tds = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
            a = tr.find("a", href=True)
            if not a or len(tds) < 5:
                if a or _text_of(tr):
                    skipped.append((f"p{page} td={len(tds)} link={'Y' if a else 'N'}", _text_of(tr)))
                continue
            sid = a["href"].rstrip("/").split("/")[-1]
            if not sid.isdigit():
                skipped.append((f"p{page} sid 비숫자 '{sid[:20]}'", _text_of(tr)))
                continue
            parsed += 1
    return "nipa", all_rows, parsed, skipped


def audit_snip():
    soup = BeautifulSoup(C.fetch(C.SNIP_URL, {"cPage": "1", "listCount": "50"}), C.PARSER)
    table = soup.find("table")
    if table is None:
        return "snip", None, [], ["표를 못 찾음"]
    rows = (table.find("tbody") or table).find_all("tr")
    parsed, skipped = 0, []
    for tr in rows:
        tds = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
        if len(tds) < 6:
            if _text_of(tr):
                skipped.append((f"td={len(tds)}", _text_of(tr)))
            continue
        if not C.SNIP_READ.search(str(tr)):
            skipped.append(("fn_read 미매칭", _text_of(tr)))
            continue
        parsed += 1
    return "snip", len(rows), parsed, skipped


def audit_kstartup(pages=1):
    all_rows, parsed, skipped = 0, 0, []
    for page in range(1, pages + 1):
        soup = BeautifulSoup(C.fetch(C.KSTARTUP_URL, {"page": str(page)}), C.PARSER)
        lis = soup.find_all("li", class_="notice")
        if not lis:
            break
        all_rows += len(lis)
        for li in lis:
            a = li.find("a", href=C.KSTARTUP_SN)
            title_el = li.find("p", class_="tit")
            if not a or not title_el:
                skipped.append((f"p{page} a={'Y' if a else 'N'} tit={'Y' if title_el else 'N'}", _text_of(li)))
                continue
            if not C.KSTARTUP_SN.search(a["href"]):
                skipped.append((f"p{page} go_view 미매칭", _text_of(li)))
                continue
            parsed += 1
    return "kstartup", all_rows, parsed, skipped


def main():
    multi = "--pages" in sys.argv
    print("소스 파싱 감사 — 목록 행수 vs 파싱 성공수\n" + "=" * 70)

    jobs = [
        audit_bizinfo,
        audit_kiria,
        (lambda: audit_nipa(C.NIPA_MAX_PAGES if multi else 1)),
        audit_snip,
        (lambda: audit_kstartup(C.KSTARTUP_MAX_PAGES if multi else 1)),
    ]

    problems = 0
    for job in jobs:
        name, rows, parsed, skipped = job()
        if rows is None:
            print(f"\n[{name}] ✗ {skipped}")
            problems += 1
            continue
        # 헤더/안내 등 링크 없는 빈 행은 정상 스킵이라 별도 표시.
        real_skips = [s for s in skipped if s[1]]
        flag = "⚠" if real_skips else "✓"
        print(f"\n[{name}] {flag} 행 {rows} / 파싱 {parsed} / 스킵 {len(skipped)}")
        if real_skips:
            problems += 1
            for reason, title in real_skips[:12]:
                print(f"    · ({reason}) {title}")
            if len(real_skips) > 12:
                print(f"    … 외 {len(real_skips) - 12}건")

    print("\n" + "=" * 70)
    print(f"의심 소스 {problems}개. ⚠ 행의 스킵 제목이 진짜 공고면 파싱 버그다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
