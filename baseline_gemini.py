"""Gemini가 작성한 원본 코드 (비교용 기준선).

로직은 그대로 두고, GPU가 없는 환경에서 돌도록 gpu=False 로만 바꾸고
결과를 results/baseline.json 으로 저장하도록 출력부만 추가했다.
"""
import cv2
import json
import os
import glob
import easyocr

reader = easyocr.Reader(['ko', 'en'], gpu=False, verbose=False)


def preprocess_image(image_path):
    image = cv2.imread(image_path)
    if image is None:
        return None, None
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced_img = clahe.apply(gray)
    return image, enhanced_img


def extract_text_and_boxes(enhanced_img, reader):
    results = reader.readtext(enhanced_img)
    extracted_data = []
    for (bbox, text, prob) in results:
        tl, tr, br, bl = bbox
        extracted_data.append({
            'text': text,
            'center_x': int((tl[0] + br[0]) / 2),
            'center_y': int((tl[1] + br[1]) / 2),
            'height': int(bl[1] - tl[1]),
        })
    return extracted_data


def build_grid(img_width, img_height, start_hour=9, end_hour=21, num_days=5):
    return {
        'day_width': img_width / num_days,
        'hour_height': img_height / (end_hour - start_hour),
        'start_hour': start_hour,
        'num_days': num_days,
        'days_list': ['월', '화', '수', '목', '금'],
    }


def match_time_and_duration(extracted_data, grid_info):
    schedule = []
    for data in extracted_data:
        day_index = int(data['center_x'] // grid_info['day_width'])
        day_index = min(max(day_index, 0), grid_info['num_days'] - 1)
        hour_offset = int(data['center_y'] // grid_info['hour_height'])
        schedule.append({
            'day': grid_info['days_list'][day_index],
            'start_time': grid_info['start_hour'] + hour_offset,
            'text': data['text'],
        })
    return schedule


if __name__ == '__main__':
    days = ['월', '화', '수', '목', '금']
    out = {}
    for image_path in sorted(glob.glob(os.path.join('timetable', 'timetable_*.png'))):
        name = os.path.basename(image_path)
        _, enhanced = preprocess_image(image_path)
        h, w = enhanced.shape
        schedule = match_time_and_duration(extract_text_and_boxes(enhanced, reader),
                                           build_grid(w, h, 9, 21))
        out[name] = {}
        print(f'=== {name} ===')
        for d in days:
            times = [c['start_time'] for c in schedule if c['day'] == d]
            out[name][d] = f'{min(times):02d}:00' if times else None
            print(f'  > {d}요일: {out[name][d] or "공강"}')
    os.makedirs('results', exist_ok=True)
    with open('results/baseline.json', 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
