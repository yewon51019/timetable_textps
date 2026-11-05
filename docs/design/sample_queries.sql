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

-- FR-02: 설정 (AC-03-04 예시값: 집→승차 10분, 탑승 여유 3분, 준비 30분 / AC-02-03: 여유 15분)
INSERT INTO routes (user_id, origin_name, mode, line_id, stop_id, direction,
                    home_to_stop_min, ride_to_school_min, board_buffer_min, arrive_buffer_min, prep_min)
VALUES (1, '집', 'subway_express', 'L1', 'S_HOME', 'down', 10, 20, 3, 15, 30);
INSERT INTO alarm_settings (user_id, stage, repeat_count, interval_min) VALUES
    (1, 'prep', 2, 10),
    (1, 'depart', 3, 5);   -- AC-04-04 예시값

-- FR-03: 교통 시각표 캐시 (가상 데이터)
INSERT INTO transit_trips (line_id, stop_id, direction, day_type, departure_time, arrival_time, is_express, valid_until) VALUES
    ('L1', 'S_HOME', 'down', 'weekday', '07:50', '08:15', TRUE,  now() + interval '7 days'),
    ('L1', 'S_HOME', 'down', 'weekday', '08:10', '08:25', TRUE,  now() + interval '7 days'),
    ('L1', 'S_HOME', 'down', 'weekday', '08:20', '08:45', FALSE, now() + interval '7 days'),
    ('L1', 'S_HOME', 'down', 'weekday', '08:30', '08:55', TRUE,  now() + interval '7 days');

-- ① 다음 수업일(월요일)의 첫 수업과 도착 목표 시각 (AC-02-03: 09:00 − 15분 = 08:45)
\echo '① 월요일 첫 수업 / 도착 목표'
SELECT d.first_class_time, d.first_class_time - make_interval(mins => r.arrive_buffer_min) AS target_arrival
FROM timetables t
JOIN timetable_days d ON d.timetable_id = t.id
JOIN routes r ON r.user_id = t.user_id
WHERE t.user_id = 1 AND t.status = 'confirmed' AND d.weekday = 0;

-- ② 목표 시각 안에 도착하는 가장 늦은 급행편 (REQ-03-02)
--    하차 08:25 + 학교까지 20분 = 08:45 ≤ 목표 08:45 → 08:10 급행 선택
--    출발 = 08:10 − 10 − 3 = 07:57, 준비 시작 = 07:57 − 30 = 07:27  (AC-03-04)
\echo '② 탑승편 · 출발 · 준비 시각'
WITH target AS (
    SELECT r.*, d.id AS day_id,
           d.first_class_time - make_interval(mins => r.arrive_buffer_min) AS target_arrival
    FROM timetables t
    JOIN timetable_days d ON d.timetable_id = t.id
    JOIN routes r ON r.user_id = t.user_id
    WHERE t.user_id = 1 AND t.status = 'confirmed' AND d.weekday = 0
), pick AS (
    SELECT tg.*, tr.id AS trip_id, tr.departure_time AS boarding_time
    FROM target tg
    JOIN transit_trips tr
      ON tr.line_id = tg.line_id AND tr.stop_id = tg.stop_id AND tr.direction = tg.direction
     AND tr.day_type = 'weekday' AND tr.is_express = (tg.mode = 'subway_express')
     AND tr.valid_until > now()
     AND tr.arrival_time + make_interval(mins => tg.ride_to_school_min) <= tg.target_arrival
    ORDER BY tr.departure_time DESC
    LIMIT 1
)
INSERT INTO commute_plans (user_id, service_date, timetable_day_id, trip_id, target_arrival,
                           boarding_time, depart_time, prep_start_time, info_basis, info_fetched_at)
SELECT user_id, DATE '2026-11-09', day_id, trip_id, target_arrival, boarding_time,
       boarding_time - make_interval(mins => home_to_stop_min + board_buffer_min),
       boarding_time - make_interval(mins => home_to_stop_min + board_buffer_min + prep_min),
       'timetable', now()
FROM pick
RETURNING service_date, target_arrival, boarding_time, depart_time, prep_start_time;

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
