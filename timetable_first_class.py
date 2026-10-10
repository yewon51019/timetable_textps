"""시간표 캡처 → 요일별 첫 수업 시작 시각 추출 (개선 버전).

핵심 아이디어
  - "수업이 어디 있는가"는 글자가 아니라 **색칠된 수업 칸(블록)** 으로 찾는다.
    (OpenCV 색 분할: HSV 채도가 높은 영역 = 수업 칸)
  - "그 위치가 몇 시인가"는 OCR로 **시간축 숫자(9, 10, 11 ...)** 를 읽어
    y좌표 ↔ 시각의 직선 관계(y = a·hour + b)를 이미지마다 새로 맞춘다.
  - 첫 수업 시각 = 그 요일 열에서 가장 위에 있는 블록의 **위쪽 경계** y를 시각으로 변환.

사용법
  python timetable_first_class.py               # timetable/timetable_*.png 전부 처리
  python timetable_first_class.py a.png b.png   # 특정 파일만
"""
import glob
import json
import os
import re
import sys

import cv2
import numpy as np

DAYS = ['월', '화', '수', '목', '금']
OUT_DIR = 'results'

_reader = None


def get_reader():
    global _reader
    if _reader is None:
        import easyocr
        _reader = easyocr.Reader(['ko', 'en'], gpu=False, verbose=False)
    return _reader


# ---------------------------------------------------------------- 1. 입력 정규화
def load_image(path):
    """RGBA/흑백 등 어떤 PNG가 와도 3채널 BGR로 맞추고, 라이트/다크 테마를 판별한다."""
    img = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    if img is None:
        raise ValueError(f'이미지를 읽을 수 없습니다: {path}')
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    elif img.shape[2] == 4:
        alpha = img[:, :, 3:4].astype(np.float32) / 255.0
        img = (img[:, :, :3] * alpha + 255 * (1 - alpha)).astype(np.uint8)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    bg = int(np.median(np.concatenate([gray[0], gray[-1], gray[:, 0], gray[:, -1]])))
    return img, gray, bg, ('dark' if bg < 128 else 'light')


# ---------------------------------------------------------------- 2. 격자(표 영역) 찾기
def _runs(mask_1d):
    """True 구간들의 [start, end] 목록."""
    idx = np.flatnonzero(np.diff(np.r_[0, mask_1d.astype(np.int8), 0]))
    return list(zip(idx[::2], idx[1::2] - 1))


def find_grid(img, gray, bg):
    """회색(무채색) 격자선을 투영(projection)해서 표 영역과 요일 열 경계를 찾는다."""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    line = (hsv[:, :, 1] < 25) & (np.abs(gray.astype(int) - bg) > 15) & (np.abs(gray.astype(int) - bg) < 120)
    h, w = gray.shape

    v_score = line.sum(axis=0) / h            # 열마다 선 픽셀 비율
    vx = [int((a + b) / 2) for a, b in _runs(v_score > 0.25)]
    h_score = line.sum(axis=1) / w
    hy = [int((a + b) / 2) for a, b in _runs(h_score > 0.5)]
    if len(vx) < 2 or len(hy) < 2:
        raise RuntimeError('격자선을 찾지 못했습니다')

    left, right = vx[0], vx[-1]
    top, bottom = hy[0], hy[-1]
    # 세로선이 정확히 6개면 그대로 쓰고, 아니면 5등분 (블록에 가려진 선 대비)
    if len(vx) == len(DAYS) + 1:
        cols = list(zip(vx[:-1], vx[1:]))
    else:
        edges = np.linspace(left, right, len(DAYS) + 1).astype(int)
        cols = list(zip(edges[:-1], edges[1:]))
    return {'left': left, 'right': right, 'top': top, 'bottom': bottom,
            'cols': cols, 'hlines': hy}


# ---------------------------------------------------------------- 3. 시간축 보정 (OCR + 직선 맞춤)
def calibrate_time_axis(img, grid, ocr_results):
    """시간축 숫자 라벨 (y_center, hour) 쌍으로 y = a*hour + b 를 맞춘다.

    - 표 왼쪽 바깥(x < grid.left)에 있는 숫자만 사용 → 수업 칸 안의 '101', '602' 같은 강의실 번호 배제
    - 이상치(OCR 오인식)는 잔차가 큰 점을 반복 제거
    - OCR이 실패하면 가로 격자선 간격 + 첫 라벨로 보완
    """
    pts = []
    for bbox, text, conf in ocr_results:
        xs = [p[0] for p in bbox]
        ys = [p[1] for p in bbox]
        if max(xs) > grid['left'] + 5:
            continue
        t = re.sub(r'[^0-9]', '', text)
        if t and 0 <= int(t) <= 24:
            pts.append((float(np.mean(ys)), int(t)))

    pts = np.array(pts, dtype=float)
    for _ in range(3):  # 간단한 이상치 제거
        if len(pts) < 3:
            break
        a, b = np.polyfit(pts[:, 1], pts[:, 0], 1)
        resid = np.abs(pts[:, 0] - (a * pts[:, 1] + b))
        if resid.max() < a * 0.25:
            break
        pts = np.delete(pts, resid.argmax(), axis=0)

    if len(pts) >= 2:
        a, b = np.polyfit(pts[:, 1], pts[:, 0], 1)
        source = f'OCR 시간 라벨 {len(pts)}개 직선 맞춤'
    else:  # 보완 경로: 격자선 간격으로 1시간 높이를 추정, 시작 시각은 9시로 가정
        gaps = np.diff(grid['hlines'])
        a = float(np.median(gaps))
        start = int(pts[0, 1]) if len(pts) else 9
        b = grid['top'] - a * start
        source = '격자선 간격 기반 추정(OCR 라벨 부족)'
    return {'a': float(a), 'b': float(b), 'points': pts.tolist(), 'source': source}


def y_to_time(y, axis, step_min=5):
    hour = (y - axis['b']) / axis['a']
    minutes = int(round(hour * 60 / step_min) * step_min)
    return minutes // 60, minutes % 60


# ---------------------------------------------------------------- 4. 수업 칸(블록) 검출
def detect_blocks(img, grid, min_h_ratio=0.3):
    """채도 높은 색 영역 = 수업 칸. 흰 글자는 구멍으로 남으므로 외곽 사각형만 쓴다."""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    mask = ((hsv[:, :, 1] > 40) & (hsv[:, :, 2] > 60)).astype(np.uint8) * 255
    roi = np.zeros_like(mask)
    roi[grid['top']:grid['bottom'] + 1, grid['left']:grid['right'] + 1] = 255
    mask = cv2.bitwise_and(mask, roi)
    # 이웃 요일 블록이 한 덩어리로 붙지 않도록 세로 격자선 위치를 끊어 준다
    for l, r in grid['cols']:
        mask[:, max(l - 3, 0):l + 4] = 0
        mask[:, max(r - 3, 0):r + 4] = 0
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))   # 잡티 제거
    # (글자 구멍은 메울 필요 없음: 외곽 사각형(bounding box)만 사용하므로)

    col_w = np.median([b - a for a, b in grid['cols']])
    blocks = []
    n, _, stats, _ = cv2.connectedComponentsWithStats(mask)
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if w < col_w * 0.5 or h < 15 or area < 0.5 * w * h:
            continue
        cx = x + w / 2
        day = next((d for d, (l, r) in enumerate(grid['cols']) if l <= cx <= r), None)
        if day is None:
            continue
        blocks.append({'day': day, 'x': int(x), 'y': int(y), 'w': int(w), 'h': int(h)})
    return blocks


def attach_text(blocks, ocr_results):
    """블록 안쪽에 시작점이 있는 OCR 글자를 과목명/강의실로 붙인다 (참고용)."""
    for blk in blocks:
        lines = []
        for bbox, text, conf in ocr_results:
            x0, y0 = bbox[0]
            if blk['x'] - 3 <= x0 <= blk['x'] + blk['w'] and blk['y'] - 3 <= y0 <= blk['y'] + blk['h']:
                lines.append((y0, text))
        blk['text'] = ' / '.join(t for _, t in sorted(lines))
    return blocks


# ---------------------------------------------------------------- 5. 전체 파이프라인
def analyze(path):
    img, gray, bg, theme = load_image(path)
    grid = find_grid(img, gray, bg)
    ocr = get_reader().readtext(img, width_ths=0.1)  # 옆 칸 글자와 한 줄로 합쳐지지 않게
    axis = calibrate_time_axis(img, grid, ocr)
    blocks = attach_text(detect_blocks(img, grid), ocr)

    result = {}
    for d, name in enumerate(DAYS):
        day_blocks = sorted([b for b in blocks if b['day'] == d], key=lambda b: b['y'])
        if not day_blocks:
            result[name] = {'time': None, 'status': '공강'}
            continue
        first = day_blocks[0]
        hh, mm = y_to_time(first['y'], axis)
        # 시간축 라벨을 3개 미만으로 읽었으면 변환식을 믿기 어려움 → 사용자 확인 필요 (PRD AC-01-03)
        status = '수업 있음' if len(axis['points']) >= 3 else '확인 필요'
        result[name] = {'time': f'{hh:02d}:{mm:02d}', 'status': status,
                        'subject': first.get('text', ''), 'top_y': first['y']}

    return {'file': os.path.basename(path), 'theme': theme, 'grid': grid,
            'axis': axis, 'blocks': blocks, 'first_class': result}, img


# ---------------------------------------------------------------- 6. 시각화
def draw(img, info, out_path):
    from PIL import Image, ImageDraw, ImageFont
    font_path = _find_korean_font()
    vis = img.copy()
    g, axis = info['grid'], info['axis']
    overlay = vis.copy()
    for blk in info['blocks']:
        cv2.rectangle(vis, (blk['x'], blk['y']), (blk['x'] + blk['w'], blk['y'] + blk['h']), (0, 200, 255), 2)
    for d, name in enumerate(DAYS):
        fc = info['first_class'][name]
        l, r = g['cols'][d]
        if fc['time']:
            y = fc['top_y']
            cv2.line(overlay, (l, y), (r, y), (0, 0, 255), 6)
        else:
            cv2.rectangle(overlay, (l + 3, g['top'] + 3), (r - 3, g['bottom'] - 3), (128, 128, 128), -1)
    vis = cv2.addWeighted(overlay, 0.6, vis, 0.4, 0)
    for y, hr in axis['points']:  # OCR로 읽은 시간 라벨 위치
        cv2.circle(vis, (8, int(y)), 5, (255, 0, 255), -1)

    # 오른쪽에 결과 패널 붙이기
    h, w = vis.shape[:2]
    panel_w = 360
    canvas = Image.new('RGB', (w + panel_w, h), (250, 250, 250))
    canvas.paste(Image.fromarray(cv2.cvtColor(vis, cv2.COLOR_BGR2RGB)), (0, 0))
    dr = ImageDraw.Draw(canvas)
    f_title = ImageFont.truetype(font_path, 30) if font_path else ImageFont.load_default()
    f_body = ImageFont.truetype(font_path, 26) if font_path else ImageFont.load_default()
    f_small = ImageFont.truetype(font_path, 18) if font_path else ImageFont.load_default()
    x0 = w + 24
    dr.text((x0, 30), info['file'], font=f_title, fill=(20, 20, 20))
    dr.text((x0, 72), f"테마: {info['theme']}", font=f_small, fill=(90, 90, 90))
    dr.text((x0, 120), '요일별 첫 수업', font=f_title, fill=(20, 20, 20))
    y = 170
    for name in DAYS:
        fc = info['first_class'][name]
        txt = f"{name}  {fc['time'] or '공강'}"
        color = (200, 30, 30) if fc['time'] else (120, 120, 120)
        dr.text((x0, y), txt, font=f_body, fill=color)
        if fc.get('subject'):
            dr.text((x0 + 4, y + 34), fc['subject'].split(' / ')[0][:16], font=f_small, fill=(90, 90, 90))
        y += 80
    dr.text((x0, y + 10), '빨간 선 = 첫 수업 위쪽 경계', font=f_small, fill=(200, 30, 30))
    dr.text((x0, y + 36), '노란 박스 = 검출된 수업 칸', font=f_small, fill=(180, 130, 0))
    dr.text((x0, y + 62), '분홍 점 = OCR 시간 라벨', font=f_small, fill=(200, 0, 200))
    dr.text((x0, y + 88), f"y = {axis['a']:.1f}·시 + {axis['b']:.0f}", font=f_small, fill=(90, 90, 90))
    canvas.save(out_path)


def _find_korean_font():
    for pat in ['/usr/share/fonts/**/NanumGothic*.ttf', '/usr/share/fonts/**/*CJK*.tt*',
                '/usr/share/fonts/**/*Noto*KR*.*', '/usr/share/fonts/**/wqy-zenhei.ttc',
                'C:/Windows/Fonts/malgun.ttf', '/System/Library/Fonts/AppleSDGothicNeo.ttc']:
        hits = glob.glob(pat, recursive=True)
        if hits:
            return sorted(hits)[0]
    return None


def main(paths):
    os.makedirs(OUT_DIR, exist_ok=True)
    summary = {}
    for p in paths:
        info, img = analyze(p)
        stem = os.path.splitext(info['file'])[0]
        draw(img, info, os.path.join(OUT_DIR, f'{stem}_result.png'))
        summary[info['file']] = {d: v['time'] for d, v in info['first_class'].items()}
        print(f"=== {info['file']} ({info['theme']}, {info['axis']['source']}) ===")
        for d in DAYS:
            fc = info['first_class'][d]
            print(f"  > {d}요일: {fc['time'] or '공강':5s}  [{fc['status']}] {fc.get('subject', '')}")
    with open(os.path.join(OUT_DIR, 'improved.json'), 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    return summary


if __name__ == '__main__':
    main(sys.argv[1:] or sorted(glob.glob(os.path.join('timetable', 'timetable_*.png'))))
