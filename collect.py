"""지원사업 공고 수집기 v0.1

기업마당(bizinfo)과 한국로봇산업진흥원(KIRIA)에서 공고 목록을 수집해
data/announcements.json 으로 저장한다.

인증키를 쓰지 않는다. 두 소스 모두 로그인 없이 열리는 공개 목록만 읽는다.

주의 — 파서는 조용히 실패하면 안 된다.
정부 사이트는 예고 없이 개편되는데, 그때 0건을 정상처럼 처리하면
"공고가 없는 날"과 구분이 안 된다. 수집 0건은 항상 에러로 올린다.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import time
from dataclasses import dataclass, asdict, field
from datetime import date, datetime, timedelta
from pathlib import Path

import requests
import yaml
from bs4 import BeautifulSoup

log = logging.getLogger("collect")

ROOT = Path(__file__).parent
OUT = ROOT / "data" / "announcements.json"
PROFILE = ROOT / "config" / "profile.yaml"

_profile = yaml.safe_load(PROFILE.read_text(encoding="utf-8"))
SEARCH_KEYWORDS: list[str] = (_profile.get("collect") or {}).get("search_keywords", [])
ARCHIVE_DAYS: int = (_profile.get("scoring") or {}).get("archive_after_days", 30)

# HTTP 헤더는 latin-1만 허용하므로 ASCII로만 쓴다.
UA = ("support-program-radar/0.1 (internal announcement monitor; "
      "contact: zzondoli@gmail.com)")
HEADERS = {"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9"}

# 정부 사이트에 부담을 주면 차단당한다. 요청 사이에 반드시 쉰다.
DELAY_SEC = 2.0
# 해외(GitHub 러너)에서 국내 정부 사이트는 국내 대비 10배쯤 느리고
# 연결이 간헐적으로 끊긴다. 넉넉히 기다리고 여러 번 재시도한다.
CONNECT_TIMEOUT = 20
READ_TIMEOUT = 30
MAX_ATTEMPTS = 4
BACKOFF_SEC = 3        # 3s → 6s → 12s

# lxml은 기업마당의 깨진 HTML(`href= "..."` 등)에서 표를 통째로 버린다.
# 느리더라도 관대한 html.parser를 쓴다.
PARSER = "html.parser"

DATE_RANGE = re.compile(r"(\d{4})[-.](\d{1,2})[-.](\d{1,2})\s*~\s*(\d{4})[-.](\d{1,2})[-.](\d{1,2})")


class CollectError(RuntimeError):
    """수집 실패. 조용히 넘어가지 않고 실행을 실패시킨다."""


@dataclass
class Announcement:
    source: str            # bizinfo | kiria
    source_id: str         # 소스 내 고유 ID (PBLN_... / IBUS_...)
    title: str
    url: str
    apply_raw: str         # 원문 그대로의 접수기간 문자열
    apply_start: str | None = None   # ISO yyyy-mm-dd
    apply_end: str | None = None
    agency: str = ""       # 소관부처·지자체
    exec_agency: str = ""  # 사업수행기관
    category: str = ""     # 지원분야
    posted_at: str = ""    # 등록일
    status: str = ""       # 소스가 알려주는 상태(진행중/대기중 등)
    collected_at: str = field(default_factory=lambda: date.today().isoformat())

    @property
    def key(self) -> str:
        return f"{self.source}:{self.source_id}"


def fetch(url: str, params: dict | None = None) -> str:
    """예의를 갖춘 GET. 일시적 오류는 한 번 재시도한다.

    연결 자체가 안 되는 경우(해외 IP 차단 등)는 재시도해도 소용없으므로
    연결 타임아웃을 읽기 타임아웃보다 짧게 잡아 빨리 실패시킨다.
    """
    last = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            r = requests.get(url, params=params, headers=HEADERS,
                             timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
            r.raise_for_status()
            if attempt > 1:
                log.info("  %d회차에 성공", attempt)
            return r.text
        except requests.RequestException as e:
            last = e
            if attempt < MAX_ATTEMPTS:
                wait = BACKOFF_SEC * (2 ** (attempt - 1))
                log.warning("요청 실패(%d/%d) %s — %ds 후 재시도",
                            attempt, MAX_ATTEMPTS, type(e).__name__, wait)
                time.sleep(wait)
            else:
                log.warning("요청 실패(%d/%d) %s — 포기",
                            attempt, MAX_ATTEMPTS, type(e).__name__)
    raise CollectError(f"요청 실패({type(last).__name__}): {url}") from last


def parse_period(raw: str) -> tuple[str | None, str | None]:
    """'2026-07-07 ~ 2026-07-24' → ISO 튜플.

    '상시 접수', '모집 완료시까지' 처럼 날짜가 아닌 표현이 흔하다.
    파싱 못 하면 None을 주되 원문(apply_raw)은 항상 보존한다.
    """
    m = DATE_RANGE.search(raw or "")
    if not m:
        return None, None
    y1, m1, d1, y2, m2, d2 = (int(x) for x in m.groups())
    try:
        return (date(y1, m1, d1).isoformat(), date(y2, m2, d2).isoformat())
    except ValueError:
        return None, None


BIZINFO_URL = "https://www.bizinfo.go.kr/sii/siia/selectSIIA200View.do"
BIZINFO_PAGE_SIZE = 15     # rows 파라미터를 늘려도 서버가 15건으로 자른다
BIZINFO_MAX_PAGES = 4      # 키워드당 최대 60건. 그 이상은 키워드가 너무 넓다는 뜻

# 검색 폼의 전체 파라미터를 갖춰야 한다. keyword만 보내면 500이 떨어진다.
BIZINFO_FORM = {
    "hashCode": "", "rowsSel": "6", "rows": "15", "cpage": "1", "cat": "",
    "schJrsdCodeTy": "", "schWntyAt": "", "schAreaDetailCodes": "",
    "schEndAt": "N",  # 마감 공고 제외
    "orderGb": "", "sort": "", "schPblancDiv": "",
    "condition": "searchPblancNm", "condition1": "AND",
    "preKeywords": "", "keyword": "", "rescan": "N",
}


def _bizinfo_rows(keyword: str = "", cpage: int = 1) -> list[Announcement]:
    """기업마당 목록 한 페이지를 파싱한다. keyword가 비면 최신 목록."""
    params = dict(BIZINFO_FORM, keyword=keyword, cpage=str(cpage))
    soup = BeautifulSoup(fetch(BIZINFO_URL, params), PARSER)

    table = next(
        (t for t in soup.find_all("table")
         if t.find("tbody") and t.find("a", href=lambda h: h and "pblancId" in h)),
        None,
    )
    if table is None:
        # 검색 결과가 0건이면 표 자체가 없을 수 있다. 최신 목록에서만 에러로 본다.
        if not keyword:
            raise CollectError("기업마당: 공고 테이블을 찾지 못했다. 사이트 개편 가능성.")
        return []

    out: list[Announcement] = []
    for tr in table.find("tbody").find_all("tr"):
        tds = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
        a = tr.find("a", href=lambda h: h and "pblancId" in h)
        if not a or len(tds) < 7:
            continue
        pid = re.search(r"pblancId=([A-Z_0-9]+)", a["href"])
        if not pid:
            continue
        raw = tds[3]
        start, end = parse_period(raw)
        out.append(Announcement(
            source="bizinfo",
            source_id=pid.group(1),
            title=a.get_text(" ", strip=True),
            url="https://www.bizinfo.go.kr/sii/siia/selectSIIA200Detail.do"
                f"?pblancId={pid.group(1)}",
            apply_raw=raw,
            apply_start=start,
            apply_end=end,
            category=tds[1],
            agency=tds[4],
            exec_agency=tds[5],
            posted_at=tds[6],
        ))
    return out


def collect_bizinfo() -> list[Announcement]:
    """최신 목록 + 키워드 검색을 합쳐 수집한다.

    최신 목록만 보면 우리 분야 공고가 15건 밖으로 밀려나면 놓친다.
    반대로 검색만 하면 예상 못 한 신규 분야를 못 잡는다. 둘 다 한다.
    """
    seen: dict[str, Announcement] = {}

    for a in _bizinfo_rows():          # 최신 목록 (발견용)
        seen[a.source_id] = a

    for kw in SEARCH_KEYWORDS:         # 키워드 검색 (정밀 추적용)
        # 한 페이지는 15건이라 결과가 많은 키워드('실증' 등)는 뒷장에 남는다.
        # 수집 단계에서 자르면 필터가 볼 기회조차 없어지므로 끝까지 넘긴다.
        total = 0
        for page in range(1, BIZINFO_MAX_PAGES + 1):
            time.sleep(DELAY_SEC)
            try:
                got = _bizinfo_rows(kw, page)
            except CollectError as e:
                log.warning("기업마당 검색 실패 kw=%s p%d: %s", kw, page, e)
                break
            for a in got:
                seen.setdefault(a.source_id, a)
            total += len(got)
            if len(got) < BIZINFO_PAGE_SIZE:   # 마지막 장
                break
        log.info("  기업마당 검색 '%s' %d건", kw, total)

    if not seen:
        raise CollectError("기업마당: 0건 수집. 파서 점검 필요.")
    return list(seen.values())


def collect_kiria() -> list[Announcement]:
    """한국로봇산업진흥원 사업공고 목록."""
    url = "https://www.kiria.org/portal/info/portalInfoBusinessList.do"
    soup = BeautifulSoup(fetch(url), PARSER)

    table = next((t for t in soup.find_all("table") if t.find("tbody")), None)
    if table is None:
        raise CollectError("KIRIA: 공고 테이블을 찾지 못했다. 사이트 개편 가능성.")

    out: list[Announcement] = []
    for tr in table.find("tbody").find_all("tr"):
        tds = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
        a = tr.find("a")
        if not a or len(tds) < 7:
            continue
        # 상세 링크가 javascript:fn_update('IBUS_...') 형태다.
        code = re.search(r"(IBUS_[0-9]+)", a.get("href", "") or "")
        if not code:
            continue
        raw = tds[2]
        start, end = parse_period(raw)
        out.append(Announcement(
            source="kiria",
            source_id=code.group(1),
            title=a.get_text(" ", strip=True),
            url="https://www.kiria.org/portal/info/portalInfoBusinessWrite.do"
                f"?mode=update&ibusCode={code.group(1)}",
            apply_raw=raw,
            apply_start=start,
            apply_end=end,
            agency="한국로봇산업진흥원",
            exec_agency="한국로봇산업진흥원",
            category="로봇",
            status=tds[3],
            posted_at=tds[6],
        ))

    if not out:
        raise CollectError("KIRIA: 0건 수집. 파서 점검 필요.")
    return out


NIPA_URL = "https://www.nipa.kr/home/2-2"
NIPA_DDAY = re.compile(r"D-(\d+)")


def collect_nipa() -> list[Announcement]:
    """정보통신산업진흥원(NIPA) 사업공고.

    목록에 접수기간이 없고 'D-31' / '종료' 형태의 잔여일만 있다.
    상세 페이지까지 들어가면 요청이 10배로 늘어나므로,
    잔여일에서 마감일을 역산하고 원문은 apply_raw에 남긴다.
    """
    soup = BeautifulSoup(fetch(NIPA_URL), PARSER)
    table = soup.find("table")
    if table is None:
        raise CollectError("NIPA: 공고 테이블을 찾지 못했다. 사이트 개편 가능성.")

    today = date.today()
    out: list[Announcement] = []
    for tr in (table.find("tbody") or table).find_all("tr"):
        tds = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
        a = tr.find("a", href=True)
        if not a or len(tds) < 5:
            continue
        sid = a["href"].rstrip("/").split("/")[-1]
        if not sid.isdigit():
            continue

        dday = tds[1]
        end = None
        m = NIPA_DDAY.search(dday)
        if m:
            end = (today + timedelta(days=int(m.group(1)))).isoformat()
        elif "종료" in dday:
            end = (today - timedelta(days=1)).isoformat()   # 이미 마감

        out.append(Announcement(
            source="nipa",
            source_id=sid,
            title=a.get_text(" ", strip=True) or tds[2],
            url=f"https://www.nipa.kr/home/2-2/{sid}",
            apply_raw=dday,
            apply_end=end,
            agency="정보통신산업진흥원",
            exec_agency="정보통신산업진흥원",
            category="ICT",
            posted_at=tds[4],
        ))

    if not out:
        raise CollectError("NIPA: 0건 수집. 파서 점검 필요.")
    return out


SNIP_URL = "https://portal.snip.or.kr:8443/user/snip/busin/businList.face"
SNIP_READ = re.compile(r"fn_read\('([^']+)','([^']+)'\)")
SNIP_DATE = re.compile(r"(\d{4})\.(\d{1,2})\.(\d{1,2})")


def collect_snip() -> list[Announcement]:
    """성남산업진흥원 사업공고.

    회사 소재지(판교)가 성남이라 지역 사업이 직접 걸린다.
    창업센터 입주 등 사무실·공간 공고도 여기에 올라온다.

    목록에 시작일이 없고 '~ 2026.08.05' 형태로 마감일만 있다.
    """
    # 기본 10건만 오면 입주 공고가 금방 밀려난다. 한 번에 넉넉히 받는다.
    soup = BeautifulSoup(fetch(SNIP_URL, {"cPage": "1", "listCount": "50"}), PARSER)
    table = soup.find("table")
    if table is None:
        raise CollectError("SNIP: 공고 테이블을 찾지 못했다. 사이트 개편 가능성.")

    out: list[Announcement] = []
    for tr in (table.find("tbody") or table).find_all("tr"):
        tds = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
        a = tr.find("a", onclick=True)
        if not a or len(tds) < 6:
            continue
        m = SNIP_READ.search(a["onclick"])
        if not m:
            continue
        annc, code = m.groups()

        raw = tds[2]
        d = SNIP_DATE.search(raw)
        end = None
        if d:
            y, mo, dy = (int(x) for x in d.groups())
            try:
                end = date(y, mo, dy).isoformat()
            except ValueError:
                pass

        # 제목 앞에 '진행중'·'마감' 같은 상태 문구가 붙어 있어 분리한다.
        title = tds[1]
        status = ""
        for tag in ("진행중", "마감임박", "마감", "예정"):
            if title.startswith(tag):
                status, title = tag, title[len(tag):].strip()
                break

        out.append(Announcement(
            source="snip",
            source_id=f"{annc}-{code}",
            title=title,
            url=("https://portal.snip.or.kr:8443/user/snip/busin/businDetail.face"
                 f"?pjtAnncSn={annc}&pjtCd={code}&stateChk=N"),
            apply_raw=" ".join(raw.split()),
            apply_end=end,
            agency="성남시",
            exec_agency="성남산업진흥원",
            category="지역",
            status=status,
            posted_at=tds[5].replace(".", "-"),
        ))

    if not out:
        raise CollectError("SNIP: 0건 수집. 파서 점검 필요.")
    return out


SOURCES = {
    "bizinfo": collect_bizinfo,
    "kiria": collect_kiria,
    "nipa": collect_nipa,
    "snip": collect_snip,
}


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(message)s",
        stream=sys.stdout,
    )

    # 실패한 소스는 지난번 수집분을 그대로 살린다.
    # 그러지 않으면 일시적 장애 한 번에 웹페이지에서 공고가 통째로 사라진다.
    previous: dict[str, list[dict]] = {}
    if OUT.exists():
        try:
            for it in json.loads(OUT.read_text(encoding="utf-8"))["items"]:
                previous.setdefault(it["source"], []).append(it)
        except (ValueError, KeyError) as e:
            log.warning("이전 수집분을 읽지 못했다: %s", e)

    items: list[dict] = []
    failures: list[str] = []

    for i, (name, fn) in enumerate(SOURCES.items()):
        if i:
            time.sleep(DELAY_SEC)
        try:
            got = fn()
            log.info("%-8s %3d건", name, len(got))
            items.extend(asdict(x) for x in got)
        except CollectError as e:
            # 한 소스가 죽어도 나머지는 살린다. 대신 종료코드로 알린다.
            log.error("%-8s 실패: %s", name, e)
            failures.append(name)
            stale = previous.get(name, [])
            if stale:
                log.warning("%-8s 지난 수집분 %d건 유지", name, len(stale))
                items.extend(stale)

    # 승인된 공고는 소스 목록에서 밀려나도 유지한다.
    # 기업마당 검색 결과는 매일 바뀌는데, 사람이 확정한 목록이 거기 휘둘리면
    # 어제 21건이 오늘 18건이 되어 목록으로서 신뢰를 잃는다.
    curated = ROOT / "config" / "curated.yaml"
    if curated.exists():
        approved = set((yaml.safe_load(curated.read_text(encoding="utf-8"))
                        or {}).get("approved") or [])
        have = {f"{i['source']}:{i['source_id']}" for i in items}
        seen_before = {f"{i['source']}:{i['source_id']}": i
                       for group in previous.values() for i in group}
        # 오래 전에 마감된 것까지 되살리면 데이터가 무한정 커진다.
        # 화면에서 내리는 기준(archive_after_days)과 같은 선을 쓴다.
        cutoff = (date.today() - timedelta(days=ARCHIVE_DAYS)).isoformat()
        restored = 0
        for key in approved - have:
            old = seen_before.get(key)
            if old and (old.get("apply_end") or "9999") >= cutoff:
                items.append(old)
                restored += 1
        if restored:
            log.info("승인 공고 %d건을 이전 수집분에서 복원", restored)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(
            {
                "collected_at": datetime.now().isoformat(timespec="seconds"),
                "failures": failures,
                "count": len(items),
                "items": items,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    log.info("저장 %s (총 %d건)", OUT.relative_to(ROOT), len(items))

    if failures:
        log.error("실패한 소스: %s", ", ".join(failures))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
