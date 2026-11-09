"""ODsay 경로 → 구간 변환과 서울/경기 분기 테스트. 응답 모양은 ODsay 레퍼런스(searchPubTransPathT) 필드 그대로."""
from transit.route_search import find_sta_order, legs_from_path, resolve_subway_codes

# 서울 출발: 서울 버스(1000) → 4호선 → 1호선 → 경기 마을버스 → 도보
SEOUL_TO_ANYANG = {'subPath': [
    {'trafficType': 3, 'sectionTime': 4},
    {'trafficType': 2, 'sectionTime': 15, 'stationCount': 8, 'intervalTime': 7,
     'lane': [{'busNo': '461', 'busLocalBlID': '100100071', 'busCityCode': 1000}],
     'startName': '서울 정류소', 'endName': '사당역', 'startLocalStationID': '120000001',
     'startArsID': '22-001', 'endLocalStationID': '120000002'},
    {'trafficType': 3, 'sectionTime': 3},
    {'trafficType': 1, 'sectionTime': 18, 'lane': [{'name': '수도권 4호선', 'subwayCode': 4}],
     'startName': '사당', 'endName': '금정'},
    {'trafficType': 3, 'sectionTime': 2},
    {'trafficType': 1, 'sectionTime': 6, 'lane': [{'name': '수도권 1호선', 'subwayCode': 1}],
     'startName': '금정', 'endName': '안양'},
    {'trafficType': 3, 'sectionTime': 2},
    {'trafficType': 2, 'sectionTime': 8, 'stationCount': 5, 'intervalTime': 12,
     'lane': [{'busNo': '2', 'busLocalBlID': '241000001', 'busCityCode': 41},
              {'busNo': '10-1', 'busLocalBlID': '241000002', 'busCityCode': 41}],
     'startName': '안양역', 'endName': '안양대학교', 'startLocalStationID': '213000001',
     'endLocalStationID': '213000002'},
    {'trafficType': 3, 'sectionTime': 0},
]}

# 경기 → 서울을 지나는 경기 버스: 정류소가 서울에 있어도 노선이 경기 소속이면 경기 API
GG_BUS_FROM_SEOUL_STOP = {'subPath': [
    {'trafficType': 2, 'sectionTime': 30, 'lane': [{'busNo': '9-3', 'busLocalBlID': '234000001', 'busCityCode': 31}],
     'startName': '사당역(서울)', 'endName': '안양대학교', 'startLocalStationID': '119000001',
     'endLocalStationID': '213000002'},
]}

# 역코드·도시코드는 가상값 (실제 값은 KRIC·ODsay 응답으로 확인)
KRIC_STATIONS = {
    '4': [{'stinNm': '사당', 'stinCd': '433', 'railOprIsttCd': 'S1'},
          {'stinNm': '금정', 'stinCd': '443', 'railOprIsttCd': 'KR'}],
    '1': [{'stinNm': '금정', 'stinCd': '1709', 'railOprIsttCd': 'KR'},
          {'stinNm': '안양', 'stinCd': '1707', 'railOprIsttCd': 'KR'}],
}


def test_kinds_and_order_kept():
    legs = legs_from_path(SEOUL_TO_ANYANG)
    assert [l['kind'] for l in legs] == ['walk', 'bus', 'walk', 'subway', 'walk', 'subway', 'walk', 'bus']


def test_bus_provider_by_route_city_code():
    legs = legs_from_path(SEOUL_TO_ANYANG)
    buses = [l for l in legs if l['kind'] == 'bus']
    assert buses[0]['bus_provider'] == 'seoul' and buses[0]['bus_route_id'] == '100100071'
    assert buses[1]['bus_provider'] == 'gyeonggi' and buses[1]['alternatives'][0]['bus_route_name'] == '10-1'
    # 서울에 있는 정류소에서 타도 경기 노선이면 경기 API
    assert legs_from_path(GG_BUS_FROM_SEOUL_STOP)[0]['bus_provider'] == 'gyeonggi'


def test_subway_codes_resolved_per_station_operator():
    legs = legs_from_path(SEOUL_TO_ANYANG)
    assert resolve_subway_codes(legs, KRIC_STATIONS) == []
    line4, line1 = [l for l in legs if l['kind'] == 'subway']
    assert (line4['board_rail_opr_cd'], line4['alight_rail_opr_cd']) == ('S1', 'KR')   # 운영기관이 바뀌는 구간
    assert line4['express_only'] is False and line1['express_only'] is True
    assert (line1['board_stin_cd'], line1['alight_stin_cd']) == ('1709', '1707')


def test_missing_station_reported():
    legs = legs_from_path(SEOUL_TO_ANYANG)
    assert resolve_subway_codes(legs, {'1': KRIC_STATIONS['1']}) == ['수도권 4호선 사당', '수도권 4호선 금정']


def test_sta_order_picks_occurrence_before_alight_on_loop_route():
    # 순환 노선: 정류소 A가 1번, 9번에 두 번 나오고 하차 B는 5번 → 1번을 골라야 함
    gg = [{'stationId': 'A', 'stationSeq': 1}, {'stationId': 'B', 'stationSeq': 5}, {'stationId': 'A', 'stationSeq': 9}]
    assert find_sta_order(gg, 'A', 'B') == 1
    seoul = [{'station': 'S', 'seq': '3'}, {'station': 'T', 'seq': '7'}]
    assert find_sta_order(seoul, 'S', 'T', id_key='station', seq_key='seq') == 3
