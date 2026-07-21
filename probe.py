"""실행 환경에서 어떤 소스에 접근 가능한지 확인한다.

기업마당이 해외 IP를 차단하는 것이 확인되면서, 나머지 후보들도
같은 상황인지 알아야 대안을 고를 수 있다. 추측 대신 실측한다.

GitHub Actions에서 수동 실행(workflow_dispatch)으로 돌린다.
"""

from __future__ import annotations

import socket
import sys
import time

import requests

UA = ("support-program-radar/0.1 (internal announcement monitor; "
      "contact: zzondoli@gmail.com)")

# (이름, URL, 비고)
TARGETS = [
    ("KIRIA",        "https://www.kiria.org/portal/info/portalInfoBusinessList.do",
     "로봇 공고 — 현재 유일한 1차 출처"),
    ("기업마당",      "https://www.bizinfo.go.kr/sii/siia/selectSIIA200View.do",
     "차단 확인됨(대조군)"),
    ("공공데이터포털", "https://www.data.go.kr/",
     "기업마당 대체 경로 후보"),
    ("공공데이터 API", "https://apis.data.go.kr/",
     "실제 API 호스트 — 이게 열리면 대안이 된다"),
    ("K-Startup",    "https://www.k-startup.go.kr/web/main.do",
     "창업 계열 공고"),
    ("중소벤처24",    "https://www.smes.go.kr/main/dbCnrs",
     "기업마당과 중복 가능"),
    ("스마트공장",    "https://www.smart-factory.kr/",
     "참고 — 봇 차단 별개 이슈"),
]


def probe(name: str, url: str, note: str) -> bool:
    host = url.split("/")[2]
    # DNS는 되는데 TCP가 막히는지, 아예 이름조차 안 풀리는지 구분한다.
    try:
        ip = socket.gethostbyname(host)
    except socket.gaierror as e:
        print(f"  {name:14} DNS 실패     {host} ({e})")
        return False

    t0 = time.monotonic()
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=(8, 20))
        ms = int((time.monotonic() - t0) * 1000)
        print(f"  {name:14} HTTP {r.status_code}  {ms:>5}ms  "
              f"{len(r.content):>7,}B  [{ip}]  — {note}")
        return r.status_code == 200
    except requests.RequestException as e:
        ms = int((time.monotonic() - t0) * 1000)
        print(f"  {name:14} {type(e).__name__:<16} {ms:>5}ms  [{ip}]  — {note}")
        return False


def main() -> int:
    print("실행 환경에서의 소스 접근성\n" + "=" * 78)
    ok = [probe(*t) for t in TARGETS]
    print("=" * 78)
    print(f"접근 가능 {sum(ok)} / {len(ok)}")
    # 진단용이므로 항상 성공으로 끝낸다. 결과는 로그로 읽는다.
    return 0


if __name__ == "__main__":
    sys.exit(main())
