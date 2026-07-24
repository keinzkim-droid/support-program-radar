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
AREA_CODES: dict[str, str] = (_profile.get("collect") or {}).get("area_codes", {})

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
    location: str = ""     # 사무실 공고의 실제 소재지(상세에서만 얻을 수 있다)
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
# 지역 훑기는 그 지역 공고를 빠짐없이 받는 것이 목적이므로 상한을 높인다.
# (경기도만 150건이 넘는다)
BIZINFO_AREA_MAX_PAGES = 14
# 연속 이만큼 실패하면 차단된 것으로 보고 기업마당 수집을 중단한다.
BIZINFO_GIVE_UP_AFTER = 2

# 검색 폼의 전체 파라미터를 갖춰야 한다. keyword만 보내면 500이 떨어진다.
BIZINFO_FORM = {
    "hashCode": "", "rowsSel": "6", "rows": "15", "cpage": "1", "cat": "",
    "schJrsdCodeTy": "", "schWntyAt": "", "schAreaDetailCodes": "",
    "schEndAt": "N",  # 마감 공고 제외
    "orderGb": "", "sort": "", "schPblancDiv": "",
    "condition": "searchPblancNm", "condition1": "AND",
    "preKeywords": "", "keyword": "", "rescan": "N",
}


def _bizinfo_rows(keyword: str = "", cpage: int = 1,
                  area: str = "") -> list[Announcement]:
    """기업마당 목록 한 페이지를 파싱한다.

    keyword가 비고 area도 비면 최신 목록,
    area만 주면 그 지역 공고 전체(제목과 무관)를 받는다.
    """
    params = dict(BIZINFO_FORM, keyword=keyword, cpage=str(cpage),
                  schAreaDetailCodes=area)
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
    """최신 목록 + 키워드 검색 + 지역 훑기를 합쳐 수집한다.

    요청이 수십 번 몰리면 정부 사이트가 해외 IP를 일시 차단한다.
    첫 요청(최신 목록)부터 막히면 이미 차단된 상태이므로, 뒤 요청을
    수십 번 더 시도하지 않고 바로 포기한다 — 차단을 악화시킬 뿐이다.
    """
    seen: dict[str, Announcement] = {}

    # 첫 요청. 여기서 막히면 지금 접속 자체가 안 되는 것이라 즉시 중단한다.
    for a in _bizinfo_rows():          # 최신 목록 (발견용)
        seen[a.source_id] = a

    # 요청 도중 차단되면 뒤를 계속 시도해봐야 다 실패한다. 연속 실패가
    # 누적되면 그만둔다. 이미 최신 목록은 받았으니 데이터는 확보돼 있다.
    fail = [0]   # 연속 실패 수 (리스트로 담아 안쪽 루프에서 갱신)

    def _sweep(label: str, keyword: str, area: str, max_pages: int) -> None:
        total = 0
        for page in range(1, max_pages + 1):
            time.sleep(DELAY_SEC)
            try:
                got = _bizinfo_rows(keyword, page, area)
                fail[0] = 0
            except CollectError:
                fail[0] += 1
                break
            for a in got:
                seen.setdefault(a.source_id, a)
            total += len(got)
            if len(got) < BIZINFO_PAGE_SIZE:   # 마지막 장
                break
        log.info("  기업마당 %s %d건", label, total)

    for kw in SEARCH_KEYWORDS:         # 키워드 검색 (전국구 사업 발견용)
        if fail[0] >= BIZINFO_GIVE_UP_AFTER:
            break
        _sweep(f"검색 '{kw}'", kw, "", BIZINFO_MAX_PAGES)

    # 지역 훑기 — 제목에 검색어가 없는 공고까지 우리 지역은 전부 받는다.
    for name, code in AREA_CODES.items():
        if fail[0] >= BIZINFO_GIVE_UP_AFTER:
            log.warning("  연속 차단으로 지역 훑기 중단 (%s부터)", name)
            break
        _sweep(f"지역 '{name}'", "", code, BIZINFO_AREA_MAX_PAGES)

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


KSTARTUP_URL = "https://www.k-startup.go.kr/web/contents/bizpbanc-ongoing.do"
KSTARTUP_MAX_PAGES = 8       # 페이지당 15건. 진행 중 공고 전체를 훑는다.
KSTARTUP_SN = re.compile(r"go_view\((\d+)\)")


def _kstartup_page(page: int) -> list[Announcement]:
    soup = BeautifulSoup(fetch(KSTARTUP_URL, {"page": str(page)}), PARSER)

    out: list[Announcement] = []
    for li in soup.find_all("li", class_="notice"):
        a = li.find("a", href=KSTARTUP_SN)
        title_el = li.find("p", class_="tit")
        if not a or not title_el:
            continue
        m = KSTARTUP_SN.search(a["href"])
        if not m:
            continue

        # <span class="list">에 '기관 / 등록일자 / 시작일자 / 마감일자'가 담긴다.
        info: dict[str, str] = {}
        agency = ""
        for sp in li.find_all("span", class_="list"):
            t = " ".join(sp.get_text(" ", strip=True).split())
            hit = re.match(r"(등록일자|시작일자|마감일자)\s*(\d{4}-\d{2}-\d{2})", t)
            if hit:
                info[hit.group(1)] = hit.group(2)
            elif t and t != title_el.get_text(strip=True) and not agency:
                agency = t

        # 분류 배지는 <span class="flag type03">. BeautifulSoup이 class를 문자열로
        # 넘기는 경우가 있어 리스트/문자열을 모두 받아 처리한다.
        def _is_flag(cls) -> bool:
            if not cls:
                return False
            names = cls if isinstance(cls, list) else str(cls).split()
            return "flag" in names and any(n.startswith("type") for n in names)

        flag = li.find("span", class_=_is_flag)
        start, end = info.get("시작일자"), info.get("마감일자")
        out.append(Announcement(
            source="kstartup",
            source_id=m.group(1),
            title=title_el.get_text(" ", strip=True),
            url=f"{KSTARTUP_URL}?schM=view&pbancSn={m.group(1)}",
            apply_raw=f"{start or ''} ~ {end or ''}".strip(" ~"),
            apply_start=start,
            apply_end=end,
            agency=agency or "중소벤처기업부",
            exec_agency=agency,
            category=flag.get_text(" ", strip=True) if flag else "창업",
            posted_at=info.get("등록일자", ""),
        ))
    return out


def collect_kstartup() -> list[Announcement]:
    """K-Startup 창업지원포털의 '모집중' 공고.

    회사가 창업 7년 이내라 대상이 된다. 분류에 '시설ㆍ공간ㆍ보육'이 있어
    창업보육센터·스타트업파크 등 입주 공고가 여기로 들어온다.
    """
    seen: dict[str, Announcement] = {}
    for page in range(1, KSTARTUP_MAX_PAGES + 1):
        if page > 1:
            time.sleep(DELAY_SEC)
        got = _kstartup_page(page)
        before = len(seen)
        for a in got:
            seen.setdefault(a.source_id, a)
        # 새로 추가된 게 없으면 마지막 장을 지난 것이다.
        if not got or len(seen) == before:
            break

    if not seen:
        raise CollectError("K-Startup: 0건 수집. 파서 점검 필요.")
    return list(seen.values())


OFFICE_HINT = re.compile(r"입주|임대|사무공간|사무실|보육센터|창업공간|공유오피스")
# 본문에서 소재지를 찾는다. '소재지 - 판교 제2테크노밸리…', '주소 : 경기도 성남시…'
ADDRESS = re.compile(r"(?:소재지|주소|위치)\s*[-:：]?\s*([^\n]{6,80})")
REGION_WORDS = ["판교", "성남", "경기도", "경기", "서울", "인천"]


def enrich_office(items: list[dict]) -> int:
    """사무실로 보이는 공고만 상세를 열어 소재지를 채운다.

    목록의 '지역'은 주관기관 기준이라 실제 사무실 위치와 다르다.
    예: '국토교통 창업지원센터'는 목록상 전국이지만 실제로는 판교 제2테크노밸리다.
    제목만으로는 판단할 수 없어 이 정보가 없으면 지역 판정이 불가능하다.

    전체를 훑으면 요청이 수백 건이 되므로 사무실 후보에만 적용한다.
    """
    targets = [i for i in items
               if OFFICE_HINT.search(i["title"]) or "시설" in (i.get("category") or "")]
    log.info("사무실 후보 %d건의 소재지를 확인한다", len(targets))

    found = 0
    for i in targets:
        if i.get("location"):
            continue
        time.sleep(DELAY_SEC)
        try:
            text = re.sub(r"\s+", " ",
                          BeautifulSoup(fetch(i["url"]), PARSER).get_text(" ", strip=True))
        except CollectError:
            continue
        for m in ADDRESS.finditer(text):
            seg = m.group(1)
            if any(w in seg for w in REGION_WORDS):
                i["location"] = seg.strip()[:80]
                found += 1
                break
    if found:
        log.info("  소재지 %d건 확인", found)
    return found


SOURCES = {
    "bizinfo": collect_bizinfo,
    "kiria": collect_kiria,
    "nipa": collect_nipa,
    "snip": collect_snip,
    "kstartup": collect_kstartup,
}


MIN_INTERVAL_HOURS = 3     # 이 시간 안에 수집했으면 건너뛴다


def _too_soon() -> float | None:
    """최근에 수집했으면 경과 시간을 돌려준다.

    예약 실행이 자꾸 밀려서 하루 6회로 시도 횟수를 늘렸다. 그대로 두면
    정부 사이트에 가는 요청도 3배가 되므로, 실제 수집은 간격을 두고 한다.
    '여러 번 시도하되 필요할 때만 수집한다'는 뜻이다.
    """
    if "--force" in sys.argv or not OUT.exists():
        return None
    try:
        prev = json.loads(OUT.read_text(encoding="utf-8"))["collected_at"]
        gap = (datetime.now() - datetime.fromisoformat(prev)).total_seconds() / 3600
    except (ValueError, KeyError, OSError):
        return None
    return gap if gap < MIN_INTERVAL_HOURS else None


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(message)s",
        stream=sys.stdout,
    )

    gap = _too_soon()
    if gap is not None:
        log.info("%.1f시간 전에 수집했다. 건너뛴다 (--force로 무시 가능)", gap)
        return 0

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
    failures: list[str] = []       # 새로 수집 실패한 소스
    data_lost: list[str] = []      # 그중 이전 수집분도 없어 실제로 비어버린 소스

    for i, (name, fn) in enumerate(SOURCES.items()):
        if i:
            time.sleep(DELAY_SEC)
        try:
            got = fn()
            log.info("%-8s %3d건", name, len(got))
            items.extend(asdict(x) for x in got)
        except CollectError as e:
            # 한 소스가 죽어도 나머지는 살린다.
            log.error("%-8s 실패: %s", name, e)
            failures.append(name)
            stale = previous.get(name, [])
            if stale:
                # 이전 수집분이 있으면 데이터는 유지된다. 정부 사이트의
                # 일시적 연결 끊김은 흔한 일이라, 이 경우는 실패로 보지 않는다.
                log.warning("%-8s 지난 수집분 %d건 유지", name, len(stale))
                items.extend(stale)
            else:
                # 이전분도 없으면 이 소스는 실제로 비었다. 이때만 경보한다.
                data_lost.append(name)

    # 사무실 공고는 제목·목록에 위치가 없어 상세를 봐야 지역을 알 수 있다.
    try:
        enrich_office(items)
    except Exception as e:                      # 부가 정보이므로 실패해도 계속한다
        log.warning("소재지 확인 실패: %s", e)

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
        log.warning("이번에 새로 수집 못 한 소스(이전분 유지): %s", ", ".join(failures))
    # 이전분마저 없어 실제로 비어버린 소스가 있을 때만 경보한다.
    # 정부 사이트의 일시적 끊김까지 메일로 알리면 경보가 무뎌진다.
    if data_lost:
        log.error("데이터가 비어버린 소스: %s", ", ".join(data_lost))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
