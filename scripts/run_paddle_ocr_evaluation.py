import json
import re
import sys
from pathlib import Path
from time import perf_counter
from typing import Any

from paddleocr import PaddleOCR

# ─────────────────────────────────────────────
# 경로 설정
# ─────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parents[1]

EVALUATION_DIR = PROJECT_ROOT / "tests" / "fixtures" / "ocr" / "evaluation"

RESULT_DIR = EVALUATION_DIR / "results" / "paddle"

RESULT_DIR.mkdir(parents=True, exist_ok=True)


# ─────────────────────────────────────────────
# 공통 함수
# ─────────────────────────────────────────────


def get_center(box: list[int]) -> tuple[float, float]:
    """글자 영역의 가운데 좌표를 계산합니다."""

    x1, y1, x2, y2 = box

    center_x = (x1 + x2) / 2
    center_y = (y1 + y2) / 2

    return center_x, center_y


def normalize_text(text: str) -> str:
    """앞뒤 공백과 불필요하게 반복된 공백을 정리합니다."""

    return re.sub(r"\s+", " ", text).strip()


def extract_number_and_unit(
    text: str,
) -> tuple[str | None, str | None]:
    """
    '1정', '2캡슐' 같은 문자열을 숫자와 단위로 분리합니다.
    """

    match = re.search(
        r"(\d+(?:\.\d+)?)\s*(정|캡슐|포|봉|개|mL|ml)",
        text,
    )

    if match is None:
        return None, None

    return match.group(1), match.group(2)


def extract_integer(text: str) -> int | None:
    """'2회', '30일'에서 숫자 부분을 꺼냅니다."""

    match = re.search(r"\d+", text)

    if match is None:
        return None

    return int(match.group())


# ─────────────────────────────────────────────
# PaddleOCR 결과 정리
# ─────────────────────────────────────────────


def make_ocr_items(raw_result: dict[str, Any]) -> list[dict[str, Any]]:
    """
    PaddleOCR의 텍스트, confidence, 좌표를 하나씩 묶습니다.
    """

    texts = raw_result["rec_texts"]
    scores = raw_result["rec_scores"]
    boxes = raw_result["rec_boxes"]

    items: list[dict[str, Any]] = []

    for text, score, box in zip(texts, scores, boxes, strict=True):
        box_list = [int(value) for value in box]
        center_x, center_y = get_center(box_list)

        items.append(
            {
                "text": normalize_text(str(text)),
                "confidence": float(score),
                "box": box_list,
                "center_x": center_x,
                "center_y": center_y,
            }
        )

    return items


def find_prescribed_date(items: list[dict[str, Any]]) -> str | None:
    """OCR 결과에서 YYYY-MM-DD 형식의 처방일자를 찾습니다."""

    for item in items:
        match = re.fullmatch(
            r"\d{4}[-./]\d{2}[-./]\d{2}",
            item["text"],
        )

        if match:
            return item["text"].replace(".", "-").replace("/", "-")

    return None


def find_table_header(  # noqa: C901
    items: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """
    문서에서 처음 발견한 처방 약품 표의 열 제목을 찾습니다.

    아래쪽에 주사제 표나 조제 표가 다시 나타나더라도
    처음 찾은 열 제목을 덮어쓰지 않습니다.
    """

    headers: dict[str, dict[str, Any]] = {}

    for item in items:
        text = re.sub(
            r"[^0-9가-힣a-zA-Z]",
            "",
            item["text"],
        )

        # 약품명 열
        if "medication_name" not in headers:
            if "처방의약품명" in text or text.startswith("의약품명") or text == "약품명":
                headers["medication_name"] = item
                continue

        # 1회 복용량 열
        if "dose" not in headers:
            if "1회투여량" in text or "1회투약량" in text or (text.startswith("1회") and "량" in text):
                headers["dose"] = item
                continue

        # 1일 복용 횟수 열
        if "frequency" not in headers:
            if "1일투여횟수" in text or "1일투약횟수" in text or (text.startswith("1일") and "횟수" in text):
                headers["frequency"] = item
                continue

        # 복용 기간 열
        if "duration" not in headers:
            if text.startswith("총"):
                headers["duration"] = item
                continue

        # 복용 시점 열
        if "timing" not in headers:
            if text == "용법" or text.startswith("용법"):
                headers["timing"] = item
                continue

    return headers


def find_table_bottom(
    items: list[dict[str, Any]],
    header_y: float,
) -> float:
    """
    약품 표가 끝나는 위치를 찾습니다.

    복약 안내, 주사제 처방내역, 조제 관련 영역은
    약품 표에 포함하지 않습니다.
    """

    stop_words = [
        "복약",
        "주사제",
        "조제 여부",
        "조제약국",
    ]

    candidates: list[float] = []

    for item in items:
        if item["center_y"] <= header_y:
            continue

        if any(word in item["text"] for word in stop_words):
            candidates.append(item["center_y"])

    if candidates:
        return min(candidates)

    return float("inf")


def group_items_by_row(
    items: list[dict[str, Any]],
    tolerance: float = 25,
) -> list[list[dict[str, Any]]]:
    """Y 좌표가 비슷한 글자들을 같은 약품 행으로 묶습니다."""

    sorted_items = sorted(items, key=lambda item: item["center_y"])
    rows: list[list[dict[str, Any]]] = []

    for item in sorted_items:
        matched_row = None

        for row in rows:
            row_center_y = sum(row_item["center_y"] for row_item in row) / len(row)

            if abs(item["center_y"] - row_center_y) <= tolerance:
                matched_row = row
                break

        if matched_row is None:
            rows.append([item])
        else:
            matched_row.append(item)

    return rows


def find_nearest_column(
    center_x: float,
    headers: dict[str, dict[str, Any]],
) -> str:
    """글자의 X 좌표와 가장 가까운 표의 열을 찾습니다."""

    return min(
        headers,
        key=lambda name: abs(center_x - headers[name]["center_x"]),
    )


def structure_medications(
    items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """약품 표의 OCR 결과를 약품별 JSON으로 변환합니다."""

    headers = find_table_header(items)

    required_headers = {
        "medication_name",
        "dose",
        "frequency",
        "duration",
        "timing",
    }

    missing_headers = required_headers - headers.keys()

    if missing_headers:
        raise ValueError("약품 표의 열 제목을 찾지 못했습니다: " + ", ".join(sorted(missing_headers)))

    header_y = max(header["center_y"] for header in headers.values())

    table_bottom = find_table_bottom(items, header_y)

    table_items = [item for item in items if header_y < item["center_y"] < table_bottom]

    rows = group_items_by_row(table_items)

    medications: list[dict[str, Any]] = []

    for row in rows:
        column_values: dict[str, list[dict[str, Any]]] = {
            "medication_name": [],
            "dose": [],
            "frequency": [],
            "duration": [],
            "timing": [],
        }

        for item in row:
            column_name = find_nearest_column(
                item["center_x"],
                headers,
            )
            column_values[column_name].append(item)

        medication_name_items = column_values["medication_name"]

        if not medication_name_items:
            continue

        medication_name = " ".join(
            item["text"]
            for item in sorted(
                medication_name_items,
                key=lambda value: value["center_x"],
            )
        )

        # 실제 약품 행인지 간단히 확인합니다.
        has_dose = bool(column_values["dose"])
        has_frequency = bool(column_values["frequency"])
        has_timing = bool(column_values["timing"])

        if not any([has_dose, has_frequency, has_timing]):
            continue

        dose_value = None
        dose_unit = None

        if column_values["dose"]:
            dose_text = column_values["dose"][0]["text"]
            dose_value, dose_unit = extract_number_and_unit(dose_text)

        frequency_per_day = None

        if column_values["frequency"]:
            frequency_per_day = extract_integer(column_values["frequency"][0]["text"])

        duration_days = None

        if column_values["duration"]:
            duration_days = extract_integer(column_values["duration"][0]["text"])

        timing = None

        if column_values["timing"]:
            timing = " ".join(
                item["text"]
                for item in sorted(
                    column_values["timing"],
                    key=lambda value: value["center_x"],
                )
            )

        medications.append(
            {
                "medication_name": medication_name,
                "dose_value": dose_value,
                "dose_unit": dose_unit,
                "frequency_per_day": frequency_per_day,
                "duration_days": duration_days,
                "timing": timing,
            }
        )

    return medications


# ─────────────────────────────────────────────
# PaddleOCR 실행
# ─────────────────────────────────────────────


def main() -> None:
    image_names = [
        "prescription_clean.png",
        "prescription_long_name.png",
        "prescription_multi_medications.png",
        "prescription_skewed.png",
        "prescription_damaged.png",
        "prescription_clean_jpg.jpg",
    ]

    force_ocr = "--force" in sys.argv
    ocr = None
    for image_name in image_names:
        result_stem = Path(image_name).stem

        raw_result_path = RESULT_DIR / f"{result_stem}_raw.json"

        structured_result_path = RESULT_DIR / f"{result_stem}_structured.json"

        print()
        print(f"다시 구조화: {image_name}")

        if force_ocr or not raw_result_path.exists():
            image_path = EVALUATION_DIR / "images" / image_name

            if ocr is None:
                model_start = perf_counter()

                ocr = PaddleOCR(
                    lang="korean",
                    use_doc_orientation_classify=False,
                    use_doc_unwarping=False,
                    use_textline_orientation=False,
                )

                model_elapsed = perf_counter() - model_start

                print(f"모델 준비 시간: {model_elapsed:.3f}초")

            print(f"PaddleOCR 실행: {image_path}")

            ocr_start = perf_counter()

            results = list(ocr.predict(str(image_path)))

            ocr_elapsed = perf_counter() - ocr_start

            print(f"순수 OCR 처리 시간: {ocr_elapsed:.3f}초")

            if not results:
                print(f"OCR 결과가 없습니다: {image_path}")
                continue

            paddle_result = results[0]

            # PaddleOCR가 result_stem_res.json으로 저장합니다.
            paddle_result.save_to_json(str(RESULT_DIR))

            paddle_result.save_to_img(str(RESULT_DIR))

            generated_result_path = RESULT_DIR / f"{result_stem}_res.json"

            if not generated_result_path.exists():
                print(f"PaddleOCR 원본 JSON을 찾지 못했습니다: {generated_result_path}")
                continue

            generated_result_path.replace(raw_result_path)

            print(f"원본 결과 저장: {raw_result_path}")

        with raw_result_path.open(
            "r",
            encoding="utf-8",
        ) as file:
            raw_result = json.load(file)

        # 저장된 JSON 구조에 res가 있으면 내부 값을 사용합니다.
        raw_result = raw_result.get("res", raw_result)

        items = make_ocr_items(raw_result)

        try:
            medications = structure_medications(items)
            structure_error = None

        except Exception as error:
            medications = []
            structure_error = str(error)

        structured_result = {
            "engine": "paddleocr",
            "image": image_name,
            "prescribed_date": find_prescribed_date(items),
            "medications": medications,
            "structure_error": structure_error,
        }

        with structured_result_path.open(
            "w",
            encoding="utf-8",
        ) as file:
            json.dump(
                structured_result,
                file,
                ensure_ascii=False,
                indent=2,
            )

        print(
            json.dumps(
                structured_result,
                ensure_ascii=False,
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
