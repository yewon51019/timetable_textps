"""ODsay 경로 검색 결과 → route_legs 구간으로 변환.

경로 '검색'은 등록할 때 한 번만 ODsay로 하고, 매일의 시각 계산은 구간별 공공 API로 한다.
ODsay가 각 구간에 지역 BIS의 노선·정류소 ID를 같이 주기 때문에 이렇게 이어 붙일 수 있다.

버스 구간이 어느 기관 API를 써야 하는지는 **정류소 위치가 아니라 노선의 소속**으로 정한다.
ODsay 문서: "수도권: busCityCode 가 1000 이면 서울, 그 외에는 경기 실시간 사용"
  → 경기 버스가 서울 정류소에서 출발해도 경기 API, 서울 버스가 경기로 들어와도 서울 API.
"""

SEOUL_CITY_CODE = 1000

# ODsay subwayCode → KRIC lnCd. 1~9호선만 확정. 그 외 노선은 KRIC 응답으로 매핑 추가 필요.
KRIC_LINE = {i: str(i) for i in range(1, 10)}


def bus_provider(lane):
    return 'seoul' if int(lane.get('busCityCode', 0)) == SEOUL_CITY_CODE else 'gyeonggi'


def legs_from_path(path):
    """ODsay path 하나 → 집에서 학교 순서의 구간 dict 목록 (route_legs 컬럼 이름 사용)."""
    legs = []
    for sp in path.get('subPath', []):
        t = int(sp['trafficType'])
        if t == 3:          # 도보
            if sp.get('sectionTime', 0) > 0:
                legs.append({'kind': 'walk', 'minutes': int(sp['sectionTime'])})
        elif t == 2:        # 버스: 같은 구간을 지나는 노선이 여러 개면 첫 번째를 기본, 나머지는 대안
            lanes = sp.get('lane', [])
            main = lanes[0]
            legs.append({
                'kind': 'bus',
                'minutes': int(sp['sectionTime']),
                'bus_provider': bus_provider(main),
                'bus_route_id': str(main.get('busLocalBlID')),
                'bus_route_name': main.get('busNo'),
                'board_station_id': str(sp.get('startLocalStationID')),
                'board_ars_id': sp.get('startArsID'),
                'alight_station_id': str(sp.get('endLocalStationID')),
                'board_stin_nm': sp.get('startName'),
                'alight_stin_nm': sp.get('endName'),
                'stop_count': sp.get('stationCount'),
                'headway_hint': sp.get('intervalTime'),   # ODsay 평균 배차, 기관 API 실패 시 참고
                'alternatives': [{'bus_route_name': l.get('busNo'), 'bus_provider': bus_provider(l),
                                  'bus_route_id': str(l.get('busLocalBlID'))} for l in lanes[1:]],
            })
        elif t == 1:        # 지하철: 역 이름만 받고, KRIC 역코드는 resolve_subway_codes 에서 채움
            lane = sp.get('lane', [{}])[0]
            code = int(lane.get('subwayCode', 0))
            legs.append({
                'kind': 'subway',
                'minutes': int(sp['sectionTime']),
                'ln_cd': KRIC_LINE.get(code),            # None 이면 매핑 필요
                'line_name': lane.get('name'),
                'board_stin_nm': sp.get('startName'),
                'alight_stin_nm': sp.get('endName'),
                'express_only': code == 1,               # MVP: 1호선만 급행 우선
            })
    return legs


def _norm(name):
    return str(name).replace(' ', '').removesuffix('역')


def resolve_subway_codes(legs, stations_by_line):
    """KRIC subwayRouteInfo 결과({lnCd: [역...]})로 역 이름 → (railOprIsttCd, stinCd) 채우기.

    같은 1호선이라도 서울역~청량리는 서울교통공사(S1), 그 밖은 한국철도(KR) 처럼
    역마다 운영기관이 달라서 역 단위로 코드를 받는다.
    """
    missing = []
    for leg in legs:
        if leg['kind'] != 'subway':
            continue
        index = {_norm(s['stinNm']): s for s in stations_by_line.get(leg['ln_cd'], [])}
        for side in ('board', 'alight'):
            s = index.get(_norm(leg[f'{side}_stin_nm']))
            if s is None:
                missing.append(f"{leg.get('line_name')} {leg[f'{side}_stin_nm']}")
                continue
            leg[f'{side}_stin_cd'] = s['stinCd']
            leg[f'{side}_rail_opr_cd'] = s.get('railOprIsttCd')
    return missing


def find_sta_order(route_stations, station_id, alight_station_id=None, id_key='stationId', seq_key='stationSeq'):
    """도착정보 조회용 정류소 순번. 순환 노선은 같은 정류소가 두 번 나오므로 하차 정류소보다 앞선 순번을 고른다.
    경기: id_key='stationId', seq_key='stationSeq' / 서울: id_key='station', seq_key='seq'"""
    seqs = [int(s[seq_key]) for s in route_stations if str(s[id_key]) == str(station_id)]
    if not seqs:
        return None
    if alight_station_id is not None:
        ends = [int(s[seq_key]) for s in route_stations if str(s[id_key]) == str(alight_station_id)]
        before = [q for q in seqs if any(e > q for e in ends)]
        if before:
            return max(before)
    return seqs[0]
