"""교통 공공 API 클라이언트.

- KRIC(철도산업정보센터, openapi.kric.go.kr): 1호선 역 코드, 열차별 운행시각표(급행 표시)
- 경기도 버스(공공데이터포털, apis.data.go.kr/6410000): 정류소·노선·배차·실시간 도착
- 서울 버스(공공데이터포털 키, ws.bus.go.kr — HTTP 전용): 노선 배차·경유 정류소·실시간 도착
- ODsay(api.odsay.com): 대중교통 경로 검색 — 경로 등록할 때만 호출

인증키는 모두 **쿼리 파라미터**로 넘긴다. 코드에 넣지 않고 환경변수로 읽는다:
  KRIC_KEY, DATA_GO_KR_KEY(경기·서울 버스 공용), ODSAY_KEY
"""
import os

import requests

TIMEOUT = 10


class ApiError(RuntimeError):
    pass


def _first_list(obj):
    """응답 JSON 안에서 처음 나오는 '객체들의 리스트'를 찾는다 (기관마다 감싸는 구조가 달라서)."""
    if isinstance(obj, list):
        if obj and all(isinstance(x, dict) for x in obj):
            return obj
        for x in obj:
            found = _first_list(x)
            if found is not None:
                return found
    elif isinstance(obj, dict):
        for v in obj.values():
            found = _first_list(v)
            if found is not None:
                return found
    return None


def _as_list(body, list_key):
    """경기도 API는 결과가 1건이면 리스트가 아니라 객체 하나로 온다 → 항상 리스트로 맞춘다."""
    v = body.get(list_key)
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


class KricClient:
    """KRIC 도시/광역철도 Open API. railOprIsttCd: KR=한국철도(코레일), S1=서울교통공사."""

    BASE = 'https://openapi.kric.go.kr/openapi'

    def __init__(self, key=None, session=None):
        self.key = key or os.environ.get('KRIC_KEY')
        if not self.key:
            raise ApiError('KRIC_KEY 환경변수가 없습니다 (openapi.kric.go.kr 에서 발급)')
        self.http = session or requests.Session()

    def _get(self, path, **params):
        r = self.http.get(f'{self.BASE}/{path}',
                          params={'serviceKey': self.key, 'format': 'json', **params}, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
        header = data.get('header') if isinstance(data, dict) else None
        if header and str(header.get('resultCode')) not in ('00', '0', '200'):
            raise ApiError(f"KRIC {path}: {header.get('resultCode')} {header.get('resultMsg')}")
        return data, _first_list(data) or []

    def route_stations(self, ln_cd='1', mrea_wide_cd='01'):
        """노선 구성역 목록 → [{stinCd, stinNm, railOprIsttCd, stinConsOrdr, ...}]"""
        return self._get('trainUseInfo/subwayRouteInfo', mreaWideCd=mrea_wide_cd, lnCd=ln_cd)[1]

    def timetable_exp(self, stin_cd, day_cd='8', ln_cd='1', opr='KR'):
        """역 하나의 열차별 운행시각표(급행 표시).
        dayCd: 8=평일, 7=토요일, 9=휴일
        → [{trnNo, arvTm, dptTm, exptCd, stinCd, ...}]  (상·하행이 섞여서 온다)"""
        return self._get('trainUseInfo/subwayTimetableExp', railOprIsttCd=opr, dayCd=day_cd,
                         lnCd=ln_cd, stinCd=stin_cd)[1]


class GyeonggiBusClient:
    """경기도 버스정보 Open API v2 (공공데이터포털)."""

    BASE = 'https://apis.data.go.kr/6410000'

    def __init__(self, key=None, session=None):
        self.key = key or os.environ.get('DATA_GO_KR_KEY')
        if not self.key:
            raise ApiError('DATA_GO_KR_KEY 환경변수가 없습니다 (data.go.kr 일반 인증키, Decoding 값)')
        self.http = session or requests.Session()

    def _get(self, path, list_key=None, **params):
        r = self.http.get(f'{self.BASE}/{path}',
                          params={'serviceKey': self.key, 'format': 'json', **params}, timeout=TIMEOUT)
        data = r.json()
        if 'OpenAPI_ServiceResponse' in data:  # 게이트웨이 단 오류 (키·트래픽)
            h = data['OpenAPI_ServiceResponse'].get('cmmMsgHeader', {})
            raise ApiError(f"data.go.kr {path}: {h.get('errMsg')} {h.get('returnAuthMsg')}")
        resp = data.get('response', data)
        head = resp.get('msgHeader', {})
        code = str(head.get('resultCode', '0'))
        if code == '4':  # 결과 없음
            return []
        if code not in ('0', '00'):
            raise ApiError(f"경기버스 {path}: {code} {head.get('resultMessage')}")
        body = resp.get('msgBody', {}) or {}
        return _as_list(body, list_key) if list_key else body

    # --- 설정 단계(사용자가 경로를 등록할 때 1회) ---
    def search_stations(self, keyword):
        return self._get('busstationservice/v2/getBusStationListv2', 'busStationList', keyword=keyword)

    def routes_via_station(self, station_id):
        return self._get('busstationservice/v2/getBusStationViaRouteListv2', 'busRouteList', stationId=station_id)

    def route_stations(self, route_id):
        return self._get('busrouteservice/v2/getBusRouteStationListv2', 'busRouteStationList', routeId=route_id)

    # --- 계획 단계(매일 새벽 캐시 갱신) ---
    def route_info(self, route_id):
        """첫차·막차, 평일 최소/최대 배차(peekAlloc / nPeekAlloc) 등."""
        items = self._get('busrouteservice/v2/getBusRouteInfoItemv2', 'busRouteInfoItem', routeId=route_id)
        return items[0] if items else None

    # --- 당일(출발 알림 전) ---
    def arrival(self, station_id, route_id, sta_order):
        """특정 정류소·노선의 다음 버스 2대 도착 예정 (predictTimeSec1/2, locationNo1/2)."""
        items = self._get('busarrivalservice/v2/getBusArrivalItemv2', 'busArrivalItem',
                          stationId=station_id, routeId=route_id, staOrder=sta_order)
        return items[0] if items else None


class SeoulBusClient:
    """서울시 버스정보 (data.go.kr 키 공용). 서울 시내버스(ODsay busCityCode=1000) 구간에 사용."""

    BASE = 'http://ws.bus.go.kr/api/rest'   # 기관이 HTTP만 제공

    def __init__(self, key=None, session=None):
        self.key = key or os.environ.get('DATA_GO_KR_KEY')
        if not self.key:
            raise ApiError('DATA_GO_KR_KEY 환경변수가 없습니다')
        self.http = session or requests.Session()

    def _get(self, path, **params):
        r = self.http.get(f'{self.BASE}/{path}',
                          params={'serviceKey': self.key, 'resultType': 'json', **params}, timeout=TIMEOUT)
        data = r.json()
        head = data.get('msgHeader') or data.get('comMsgHeader') or {}
        if str(head.get('headerCd', '0')) not in ('0', '4'):   # 4 = 결과 없음
            raise ApiError(f"서울버스 {path}: {head.get('headerCd')} {head.get('headerMsg')}")
        items = (data.get('msgBody') or {}).get('itemList') or []
        return items if isinstance(items, list) else [items]

    def route_info(self, bus_route_id):
        """배차간격 term(분), 첫차 firstBusTm, 막차 lastBusTm."""
        items = self._get('busRouteInfo/getRouteInfo', busRouteId=bus_route_id)
        return items[0] if items else None

    def route_stations(self, bus_route_id):
        """경유 정류소: seq(순번), station(정류소 고유 ID = stId), arsId, stationNm."""
        return self._get('busRouteInfo/getStaionByRoute', busRouteId=bus_route_id)

    def arrival(self, st_id, bus_route_id, ord_):
        """다음 버스 2대: exps1/exps2(도착 예정 초), traTime1, arrmsg1."""
        items = self._get('arrive/getArrInfoByRoute', stId=st_id, busRouteId=bus_route_id, ord=ord_)
        return items[0] if items else None


class OdsayClient:
    """ODsay 대중교통 길찾기. 키는 서버 IP 등록 방식(플랫폼 '서버', 최대 5개)."""

    BASE = 'https://api.odsay.com/v1/api'

    def __init__(self, key=None, session=None):
        self.key = key or os.environ.get('ODSAY_KEY')
        if not self.key:
            raise ApiError('ODSAY_KEY 환경변수가 없습니다 (lab.odsay.com 에서 발급)')
        self.http = session or requests.Session()

    def search_paths(self, sx, sy, ex, ey, path_type=0):
        """도시내 경로 후보. path_type: 0 전체, 1 지하철만, 2 버스만. 좌표는 WGS84 (경도, 위도)."""
        r = self.http.get(f'{self.BASE}/searchPubTransPathT', params={
            'apiKey': self.key, 'SX': sx, 'SY': sy, 'EX': ex, 'EY': ey,
            'OPT': 0, 'SearchPathType': path_type}, timeout=TIMEOUT)
        data = r.json()
        if 'error' in data:
            raise ApiError(f"ODsay: {data['error']}")
        return data['result'].get('path', [])
