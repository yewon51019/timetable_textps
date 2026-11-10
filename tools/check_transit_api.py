"""교통 API 호출 확인 스크립트.

  # 키 없이: 엔드포인트가 실제로 존재하는지만 확인
  python -m tools.check_transit_api --probe

  # ODsay 경로 → 구간 변환 → KRIC·서울/경기 버스 ID 연결 확인 (ODSAY_KEY 필요, 나머지 키는 있으면 추가 확인)
  python -m tools.check_transit_api --odsay --from-x 126.98 --from-y 37.48 --to-x <안양대 경도> --to-y <안양대 위도>

  # 키가 있으면: 설정 → 계획 → 당일 순서로 실제 호출하고 응답 샘플을 저장
  export KRIC_KEY=...          # openapi.kric.go.kr 에서 발급
  export DATA_GO_KR_KEY=...    # data.go.kr 일반 인증키(Decoding)
  python -m tools.check_transit_api --board 금정 --alight 안양 --bus-from 안양역 --bus-to 안양대학교 --bus-route 2

응답 원본은 docs/design/api_samples/ 에 저장된다 (인증키는 저장하지 않음).
"""
import argparse
import collections
import json
import os
import sys

import requests

from transit.clients import ApiError, GyeonggiBusClient, KricClient, OdsayClient, SeoulBusClient
from transit.planner import Leg, fmt, join_trips, plan_commute
from transit.route_search import find_sta_order, legs_from_path, resolve_subway_codes

OUT = os.path.join('docs', 'design', 'api_samples')

PROBES = [  # (설명, URL) — 가짜 키로 불러서 '키 오류'가 오면 엔드포인트는 존재
    ('경기 정류소 검색', 'https://apis.data.go.kr/6410000/busstationservice/v2/getBusStationListv2?keyword=x'),
    ('경기 정류소 경유노선', 'https://apis.data.go.kr/6410000/busstationservice/v2/getBusStationViaRouteListv2?stationId=1'),
    ('경기 노선 경유정류소', 'https://apis.data.go.kr/6410000/busrouteservice/v2/getBusRouteStationListv2?routeId=1'),
    ('경기 노선 정보(배차)', 'https://apis.data.go.kr/6410000/busrouteservice/v2/getBusRouteInfoItemv2?routeId=1'),
    ('경기 버스 도착(항목)', 'https://apis.data.go.kr/6410000/busarrivalservice/v2/getBusArrivalItemv2?stationId=1&routeId=1&staOrder=1'),
    ('TAGO 도시코드 목록', 'https://apis.data.go.kr/1613000/BusRouteInfoInqireService/getCtyCodeList?_type=json'),
    ('(대조군) 없는 오퍼레이션', 'https://apis.data.go.kr/6410000/busrouteservice/v2/notExist?routeId=1'),
]


def probe():
    print('키 없이 엔드포인트 존재 확인 (data.go.kr은 없는 오퍼레이션이면 NO_OPENAPI_SERVICE_ERROR)')
    for name, url in PROBES:
        for _ in range(3):  # 프록시·게이트웨이 연결 끊김이 가끔 있어 재시도
            try:
                r = requests.get(url, params={'serviceKey': 'invalid', 'format': 'json'}, timeout=15)
                msg = r.json().get('OpenAPI_ServiceResponse', {}).get('cmmMsgHeader', {}).get('errMsg', r.status_code)
                break
            except Exception as e:  # noqa: BLE001 — 네트워크 오류도 그대로 보여 준다
                msg = f'요청 실패: {e.__class__.__name__}'
        ok = '존재' if msg == 'SERVICE_KEY_IS_NOT_REGISTERED_ERROR' else '없음/오류'
        print(f'  {name:22s} → {msg}  [{ok}]')
    print('KRIC는 없는 오퍼레이션에도 "등록되지 않은 서비스키"를 돌려주므로 키 없이는 확인 불가')


def save(name, data):
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, f'{name}.json'), 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def find_station(stations, name):
    exact = [s for s in stations if s.get('stinNm') == name]
    return (exact or [s for s in stations if name in str(s.get('stinNm'))] or [None])[0]


def check_subway(args):
    print('\n[지하철] KRIC')
    kric = KricClient()
    stations = kric.route_stations(ln_cd=args.ln_cd)
    save('kric_subwayRouteInfo_1호선', stations)
    print(f'  ① subwayRouteInfo: 1호선 구성역 {len(stations)}개')
    picked = {}
    for nm in (args.board, args.alight, args.local_only):
        s = find_station(stations, nm)
        picked[nm] = s
        print(f'     {nm}: {s and {k: s.get(k) for k in ("stinCd", "railOprIsttCd", "stinConsOrdr")}}')
    if not picked[args.board] or not picked[args.alight]:
        sys.exit('  역을 찾지 못했습니다 — 위 저장 파일에서 역명을 확인하세요')

    rows = {}
    for nm, s in picked.items():
        if not s:
            continue
        rows[nm] = kric.timetable_exp(s['stinCd'], day_cd='8', ln_cd=args.ln_cd,
                                      opr=s.get('railOprIsttCd') or 'KR')
        save(f'kric_subwayTimetableExp_{nm}_평일', rows[nm])
        dist = collections.Counter(str(r.get('exptCd')) for r in rows[nm])
        print(f'  ② subwayTimetableExp {nm}: {len(rows[nm])}편, exptCd 분포 {dict(dist)}')
    print(f'     → 완행 전용역({args.local_only})에 없는 exptCd 값이 "급행" 코드입니다. planner.EXPRESS_CODES 와 비교하세요')

    trips = join_trips(rows[args.board], rows[args.alight])
    print(f'  ③ 열차번호로 맞춘 {args.board}→{args.alight} 열차 {len(trips)}편 (07:00~09:30 예시)')
    for t in trips:
        if 7 * 60 <= t.board_dep <= 9 * 60 + 30:
            print(f'     {t.trn_no:>6}  {fmt(t.board_dep)} → {fmt(t.alight_arr)}  {"급행" if t.is_express else "일반"}')
    return trips


def check_bus(args):
    print('\n[버스] 경기도 버스정보 v2')
    bus = GyeonggiBusClient()
    frm, to = bus.search_stations(args.bus_from), bus.search_stations(args.bus_to)
    save(f'gg_getBusStationListv2_{args.bus_from}', frm)
    save(f'gg_getBusStationListv2_{args.bus_to}', to)
    print(f'  ① 정류소 검색: {args.bus_from} {len(frm)}곳, {args.bus_to} {len(to)}곳')
    to_ids = {s['stationId'] for s in to}

    for s in frm:
        for r in bus.routes_via_station(s['stationId']):
            if str(r.get('routeName')) != args.bus_route:
                continue
            seq = bus.route_stations(r['routeId'])
            save(f"gg_getBusRouteStationListv2_{r['routeId']}", seq)
            order = {x['stationId']: int(x['stationSeq']) for x in seq}
            here = int(r['staOrder'])
            dest = [order[i] for i in to_ids if i in order and order[i] > here]
            if not dest:
                continue
            info = bus.route_info(r['routeId'])
            save(f"gg_getBusRouteInfoItemv2_{r['routeId']}", info)
            arr = bus.arrival(s['stationId'], r['routeId'], here)
            save(f"gg_getBusArrivalItemv2_{r['routeId']}", arr)
            stops = min(dest) - here
            print(f"  ② {args.bus_from}({s['stationId']}, {s.get('mobileNo')}) → {args.bus_to}: "
                  f"노선 {r['routeName']}({r['routeId']}) {stops}정거장")
            print(f"  ③ 노선정보: 평일 첫차 {info.get('upFirstTime')} 막차 {info.get('upLastTime')} "
                  f"배차 {info.get('peekAlloc')}~{info.get('nPeekAlloc')}분")
            print(f"  ④ 실시간: {arr and (arr.get('predictTimeSec1'), arr.get('locationNo1'))} (초, 몇 정거장 전)")
            return {'stops': stops, 'headway_max': int(info.get('nPeekAlloc') or 0) or None}
    print(f'  {args.bus_from} → {args.bus_to} 방향으로 가는 {args.bus_route}번을 찾지 못했습니다')
    return None


def check_tago_cities():
    """TAGO 버스가 서울·경기를 지원하는지: 도시코드 목록에 서울/경기 시·군이 있는지 확인."""
    key = os.environ.get('DATA_GO_KR_KEY')
    if not key:
        sys.exit('DATA_GO_KR_KEY 환경변수가 없습니다 ("국토교통부_(TAGO)_버스노선정보" 활용신청 필요)')
    r = requests.get('https://apis.data.go.kr/1613000/BusRouteInfoInqireService/getCtyCodeList',
                     params={'serviceKey': key, '_type': 'json'}, timeout=15)
    data = r.json()
    if 'OpenAPI_ServiceResponse' in data:
        sys.exit(f"TAGO: {data['OpenAPI_ServiceResponse'].get('cmmMsgHeader', {}).get('errMsg')}")
    items = data['response']['body']['items']['item']
    items = items if isinstance(items, list) else [items]
    save('tago_getCtyCodeList', items)
    print(f'TAGO 지원 도시 {len(items)}곳 (전체 목록은 api_samples/tago_getCtyCodeList.json)')
    for want in ('서울', '안양', '군포', '의왕', '과천', '수원', '성남', '경기'):
        hits = [f"{c['cityname']}({c['citycode']})" for c in items if want in str(c['cityname'])]
        print(f"  {want:4s}: {', '.join(hits) or '없음'}")


def check_odsay(args):
    """ODsay 경로 검색 → 구간 변환 → (키가 있으면) KRIC 역코드·서울/경기 노선ID가 실제로 이어지는지 확인."""
    od = OdsayClient()
    paths = od.search_paths(args.from_x, args.from_y, args.to_x, args.to_y)
    save('odsay_searchPubTransPathT', paths)
    print(f'[ODsay] 경로 후보 {len(paths)}개')
    for i, p in enumerate(paths[:5]):
        kinds = ' → '.join({1: '지하철', 2: '버스', 3: '도보'}[int(sp['trafficType'])] for sp in p['subPath'])
        print(f"  {i}: {p['info'].get('totalTime')}분, {kinds}")
    if not paths:
        return
    legs = legs_from_path(paths[args.path])
    print(f'\n[변환] 경로 {args.path} → 구간 {len(legs)}개')
    for l in legs:
        extra = {k: l.get(k) for k in ('bus_provider', 'bus_route_name', 'bus_route_id', 'line_name',
                                       'board_stin_nm', 'alight_stin_nm') if l.get(k)}
        print(f"  {l['kind']:6s} {l.get('minutes')}분 {extra}")

    if any(l['kind'] == 'subway' for l in legs) and os.environ.get('KRIC_KEY'):
        kric = KricClient()
        lines = {l['ln_cd'] for l in legs if l['kind'] == 'subway' and l['ln_cd']}
        missing = resolve_subway_codes(legs, {ln: kric.route_stations(ln_cd=ln) for ln in lines})
        print(f"\n[KRIC] 역코드 매핑: {'모두 성공' if not missing else '실패 ' + ', '.join(missing)}")

    if os.environ.get('DATA_GO_KR_KEY'):
        clients = {'gyeonggi': GyeonggiBusClient(), 'seoul': SeoulBusClient()}
        for l in [x for x in legs if x['kind'] == 'bus']:
            c = clients[l['bus_provider']]
            try:  # ODsay busLocalBlID 가 그 기관의 노선ID와 같은 값인지 = 실제로 조회되는지
                seq = c.route_stations(l['bus_route_id'])
                if l['bus_provider'] == 'gyeonggi':
                    order = find_sta_order(seq, l['board_station_id'], l['alight_station_id'])
                else:
                    order = find_sta_order(seq, l['board_station_id'], l['alight_station_id'], 'station', 'seq')
                ok = '성공' if seq and order else '노선은 조회됐지만 정류소 순번 못 찾음' if seq else '노선 조회 결과 없음'
                print(f"[{l['bus_provider']}] {l['bus_route_name']}번 노선ID {l['bus_route_id']} → {ok} (순번 {order})")
            except Exception as e:  # noqa: BLE001 — 서울 API는 HTTP 전용이라 환경에 따라 실패할 수 있음
                print(f"[{l['bus_provider']}] {l['bus_route_name']}번 조회 실패: {e.__class__.__name__}: {e}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--probe', action='store_true')
    ap.add_argument('--tago-cities', action='store_true', help='TAGO 버스의 서울·경기 지원 여부 확인')
    ap.add_argument('--odsay', action='store_true', help='ODsay 경로 검색 → 구간 변환 → 기관 API 연결 확인')
    ap.add_argument('--from-x', type=float, help='출발 경도')
    ap.add_argument('--from-y', type=float, help='출발 위도')
    ap.add_argument('--to-x', type=float, help='도착 경도 (안양대 정문 좌표를 지도에서 확인해 입력)')
    ap.add_argument('--to-y', type=float, help='도착 위도')
    ap.add_argument('--path', type=int, default=0, help='변환해 볼 경로 후보 번호')
    ap.add_argument('--ln-cd', default='1')
    ap.add_argument('--board', default='금정')
    ap.add_argument('--alight', default='안양')
    ap.add_argument('--local-only', default='명학', help='급행이 서지 않는 역 (급행 코드 확인용)')
    ap.add_argument('--bus-from', default='안양역')
    ap.add_argument('--bus-to', default='안양대학교')
    ap.add_argument('--bus-route', default='2')
    args = ap.parse_args()

    if args.probe:
        return probe()
    if args.tago_cities:
        return check_tago_cities()
    if args.odsay:
        if None in (args.from_x, args.from_y, args.to_x, args.to_y):
            sys.exit('--from-x --from-y --to-x --to-y 좌표가 필요합니다')
        try:
            return check_odsay(args)
        except ApiError as e:
            sys.exit(f'API 오류: {e}')
    try:
        trips = check_subway(args)
        bus = check_bus(args)
    except ApiError as e:
        sys.exit(f'API 오류: {e}')

    print('\n[조합] 첫 수업 09:00, 수업 전 여유 15분, 탑승 여유 3분, 준비 30분')
    legs = [Leg('walk', '집→승차역', 10), Leg('subway', '1호선 급행', trips=trips)]
    if bus:
        legs += [Leg('walk', '역→정류소', 3),
                 Leg('bus', f'버스 {args.bus_route}', minutes=round(bus['stops'] * 1.5), headway_max=bus['headway_max']),
                 Leg('walk', '정류소→강의실', 5)]
    else:
        legs += [Leg('walk', '역→강의실(도보)', 20)]
    p = plan_commute('09:00', legs, 15, 3, 30)
    print(json.dumps(p.__dict__, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
