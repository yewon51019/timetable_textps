"""정답(사람이 직접 읽은 값)과 비교해 요일 단위 정답률을 계산한다.

PRD 평가지표: 요일 단위 정답 일치율 = 정확한 HH:MM 또는 공강을 맞힌 요일 수 / 전체 요일 수 × 100
"""
import json
import os

import cv2

DAYS = ['월', '화', '수', '목', '금']
GROUND_TRUTH = {
    'timetable_1.png': {'월': '09:00', '화': '09:00', '수': '09:00', '목': '09:00', '금': '09:00'},
    'timetable_2.png': {'월': '13:00', '화': '13:00', '수': '15:00', '목': '13:00', '금': '14:00'},
    'timetable_3.png': {'월': '10:00', '화': '10:00', '수': None, '목': '09:00', '금': None},
    'timetable_4.png': {'월': '15:00', '화': '12:00', '수': '12:00', '목': '15:00', '금': '15:00'},
    'timetable_5.png': {'월': '09:00', '화': '15:00', '수': '09:00', '목': None, '금': '15:00'},
}


def score(pred):
    ok = sum(pred.get(f, {}).get(d) == GROUND_TRUTH[f][d] for f in GROUND_TRUTH for d in DAYS)
    return ok, len(GROUND_TRUTH) * len(DAYS)


def fmt(v):
    return v or '공강'


def robustness(scales=(0.6, 1.5)):
    """해상도를 바꿔도 결과가 같은지: 원본을 축소/확대해서 다시 돌린다."""
    import timetable_first_class as tfc
    tmp = os.path.join('results', '_scaled')
    os.makedirs(tmp, exist_ok=True)
    out = {}
    for s in scales:
        pred = {}
        for f in GROUND_TRUTH:
            img = cv2.imread(os.path.join('timetable', f))
            img = cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_CUBIC)
            p = os.path.join(tmp, f)
            cv2.imwrite(p, img)
            info, _ = tfc.analyze(p)
            pred[f] = {d: v['time'] for d, v in info['first_class'].items()}
        out[s] = score(pred)
    return out


def main():
    with open('results/baseline.json', encoding='utf-8') as f:
        base = json.load(f)
    with open('results/improved.json', encoding='utf-8') as f:
        imp = json.load(f)

    lines = ['# 요일별 첫 수업 시각 — 정답 비교', '',
             '| 파일 | 요일 | 정답 | Gemini 원본 | 개선 버전 |', '|---|---|---|---|---|']
    for f in GROUND_TRUTH:
        for d in DAYS:
            gt, b, i = GROUND_TRUTH[f][d], base[f][d], imp[f][d]
            mark = lambda v: fmt(v) + (' ✅' if v == gt else ' ❌')
            lines.append(f'| {f} | {d} | {fmt(gt)} | {mark(b)} | {mark(i)} |')
    bo, n = score(base)
    io, _ = score(imp)
    lines += ['', f'- Gemini 원본: **{bo}/{n} = {bo / n * 100:.0f}%**',
              f'- 개선 버전: **{io}/{n} = {io / n * 100:.0f}%**']

    rb = robustness()
    for s, (ok, n) in rb.items():
        lines.append(f'- 개선 버전, 이미지 {s}배 크기로 변경: **{ok}/{n} = {ok / n * 100:.0f}%**')

    text = '\n'.join(lines) + '\n'
    with open('results/evaluation.md', 'w', encoding='utf-8') as f:
        f.write(text)
    print(text)


if __name__ == '__main__':
    main()
