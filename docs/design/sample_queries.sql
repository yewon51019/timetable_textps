-- schema.sql 검증용 예시 데이터와 핵심 쿼리.
-- 시간표는 results/ 의 timetable_5.png 인식 결과를 사용하고,
-- 계산 결과가 PRD 수용 기준(AC-02-03, AC-03-04, AC-04-04)과 같은지 확인한다.

INSERT INTO users (device_id) VALUES ('anon-device-001');

-- FR-01: 인식 결과 저장 → 사용자가 확인하면 confirmed
INSERT INTO timetables (user_id, status, theme, confirmed_at) VALUES (1, 'confirmed', 'dark', now());
INSERT INTO timetable_days (timetable_id, weekday, first_class_time, status, confidence) VALUES
    (1, 0, '09:00', 'class', 0.98),
    (1, 1, '15:00', 'class', 0.97),
    (1, 2, '09:00', 'class', 0.98),
    (1, 3, NULL,    'free',  NULL),
    (1, 4, '15:00', 'class', 0.96);

-- FR-02: 경로 = 걷기 10분 → 1호선 급행(금정→안양) → 걷기 20분  (AC-03-04 예시값: 탑승 여유 3, 준비 30 / AC-02-03: 여유 15)
INSERT INTO routes (user_id, arrive_buffer_min, board_buffer_min, prep_min) VALUES (1, 15, 3, 30);
INSERT INTO route_legs (route_id, seq, kind, minutes) VALUES (1, 1, 'walk', 10);
INSERT INTO route_legs (route_id, seq, kind, board_rail_opr_cd, alight_rail_opr_cd, ln_cd, board_stin_cd, board_stin_nm, alight_stin_cd, alight_stin_nm)
VALUES (1, 2, 'subway', 'KR', 'KR', '1', 'GJ', '금정', 'AY', '안양');   -- 역코드는 가상값 (실제 값은 KRIC 응답으로 채움)
INSERT INTO route_legs (route_id, seq, kind, minutes) VALUES (1, 3, 'walk', 20);
INSERT INTO alarm_settings (user_id, stage, repeat_count, interval_min) VALUES
    (1, 'prep', 2, 10),
    (1, 'depart', 3, 5);   -- AC-04-04 예시값

-- FR-03: KRIC subwayTimetableExp 캐시 (가상 데이터, 승차역·하차역 각각)
INSERT INTO subway_timetables (rail_opr_cd, ln_cd, stin_cd, day_cd, trn_no, arv_tm, dpt_tm, is_express, expt_cd) VALUES
    ('KR', '1', 'GJ', '8', 'K101', '08:09:30', '08:10', TRUE,  '1'),
    ('KR', '1', 'GJ', '8', 'K103', '08:19:30', '08:20', FALSE, '0'),
    ('KR', '1', 'GJ', '8', 'K105', '08:29:30', '08:30', TRUE,  '1'),
    ('KR', '1', 'GJ', '8', 'K102', '08:14:30', '08:15', FALSE, '0'),   -- 반대 방향
    ('KR', '1', 'AY', '8', 'K101', '08:25', '08:25:30', TRUE,  '1'),
    ('KR', '1', 'AY', '8', 'K103', '08:40', '08:40:30', FALSE, '0'),
    ('KR', '1', 'AY', '8', 'K105', '08:45', '08:45:30', TRUE,  '1'),
    ('KR', '1', 'AY', '8', 'K102', '08:00', '08:00:30', FALSE, '0');

-- ① 다음 수업일(월요일)의 첫 수업과 도착 목표 시각 (AC-02-03: 09:00 − 15분 = 08:45)
\echo '① 월요일 첫 수업 / 도착 목표'
SELECT d.first_class_time, d.first_class_time - make_interval(mins => r.arrive_buffer_min) AS target_arrival
FROM timetables t
JOIN timetable_days d ON d.timetable_id = t.id
JOIN routes r ON r.user_id = t.user_id
WHERE t.user_id = 1 AND t.status = 'confirmed' AND d.weekday = 0;

-- ② 승차역·하차역 시각표를 열차번호로 조인 → 하차 + 이후 걷기 ≤ 목표 인 급행 중 가장 늦은 열차
--    반대 방향(K102)은 하차역 도착이 승차역 출발보다 빨라 자동으로 빠진다
--    08:10 급행 → 08:25 안양 + 20분 = 08:45 OK → 출발 = 08:10 − 3 − 10 = 07:57, 준비 = 07:27 (AC-03-04)
--    (실서비스에서는 이 계산을 transit/planner.py 가 하고, 여기서는 SQL로도 같은 결과가 나오는지 확인)
\echo '② 열차 조인 결과와 선택된 열차'
WITH r AS (
    SELECT rt.*, d.id AS day_id, d.first_class_time - make_interval(mins => rt.arrive_buffer_min) AS target,
           s.board_rail_opr_cd, s.alight_rail_opr_cd, s.ln_cd, s.board_stin_cd, s.alight_stin_cd, s.express_only,
           (SELECT minutes FROM route_legs WHERE route_id = rt.id AND seq = 1) AS walk_before,
           (SELECT minutes FROM route_legs WHERE route_id = rt.id AND seq = 3) AS walk_after
    FROM routes rt
    JOIN route_legs s ON s.route_id = rt.id AND s.kind = 'subway'
    JOIN timetables t ON t.user_id = rt.user_id AND t.status = 'confirmed'
    JOIN timetable_days d ON d.timetable_id = t.id AND d.weekday = 0
    WHERE rt.user_id = 1
), trips AS (
    SELECT b.trn_no, b.dpt_tm AS board_dep, a.arv_tm AS alight_arr, b.is_express
    FROM r
    JOIN subway_timetables b ON (b.rail_opr_cd, b.ln_cd, b.stin_cd, b.day_cd) = (r.board_rail_opr_cd, r.ln_cd, r.board_stin_cd, '8')
    JOIN subway_timetables a ON (a.rail_opr_cd, a.ln_cd, a.stin_cd, a.day_cd, a.trn_no) = (r.alight_rail_opr_cd, r.ln_cd, r.alight_stin_cd, '8', b.trn_no)
    WHERE a.arv_tm > b.dpt_tm
), pick AS (
    SELECT trips.* FROM trips, r
    WHERE (trips.is_express OR NOT r.express_only)
      AND trips.alight_arr + make_interval(mins => r.walk_after) <= r.target
    ORDER BY board_dep DESC LIMIT 1
)
INSERT INTO commute_plans (user_id, service_date, timetable_day_id, target_arrival, depart_time,
                           prep_start_time, subway_trn_no, info_basis, info_fetched_at)
SELECT r.user_id, DATE '2026-11-09', r.day_id, r.target,
       p.board_dep - make_interval(mins => r.board_buffer_min + r.walk_before),
       p.board_dep - make_interval(mins => r.board_buffer_min + r.walk_before + r.prep_min),
       p.trn_no, 'timetable', now()
FROM r, pick p
RETURNING service_date, target_arrival, subway_trn_no, depart_time, prep_start_time;

-- ③ 단계별 알림 예약: 기준 − (n−1)k … 기준 (REQ-04-02)
--    출발 07:57, 3회, 5분 → 07:47 · 07:52 · 07:57
\echo '③ 알림 예약 목록'
INSERT INTO notifications (plan_id, stage, seq, scheduled_at)
SELECT p.id, a.stage, s.seq,
       (p.service_date + CASE a.stage WHEN 'prep' THEN p.prep_start_time ELSE p.depart_time END)
         - make_interval(mins => (a.repeat_count - s.seq) * a.interval_min)
FROM commute_plans p
JOIN alarm_settings a ON a.user_id = p.user_id
CROSS JOIN LATERAL generate_series(1, a.repeat_count) AS s(seq)
WHERE p.id = 1
ON CONFLICT (plan_id, stage, seq) DO UPDATE SET scheduled_at = EXCLUDED.scheduled_at, status = 'scheduled';

SELECT stage, seq, to_char(scheduled_at AT TIME ZONE 'Asia/Seoul', 'HH24:MI') AS at, status
FROM notifications ORDER BY scheduled_at;

-- ④ 준비 확인 버튼 → 남은 준비 알림만 취소, 출발 알림 유지 (AC-04-02)
\echo '④ 준비 확인 후'
UPDATE notifications SET status = 'cancelled', cancel_reason = 'prep_confirmed'
WHERE plan_id = 1 AND stage = 'prep' AND status = 'scheduled';
SELECT stage, count(*) FILTER (WHERE status = 'scheduled') AS remaining FROM notifications GROUP BY stage ORDER BY stage;

-- ⑤ 제약 확인: 수업 있음인데 시각이 비어 있으면 거부돼야 한다
\echo '⑤ 잘못된 데이터 거부 확인 (오류가 나야 정상)'
INSERT INTO timetable_days (timetable_id, weekday, first_class_time, status) VALUES (1, 3, NULL, 'class');
