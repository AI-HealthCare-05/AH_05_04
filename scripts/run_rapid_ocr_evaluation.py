import json
import re
from importlib.metadata import version
from pathlib import Path

from rapidocr import (
    EngineType,
    LangDet,
    LangRec,
    ModelType,
    OCRVersion,
    RapidOCR,
)

# ─────────────────────────────────────────────
# 경로 설정
# ─────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parents[1]

EVALUATION_DIR = PROJECT_ROOT / "tests" / "fixtures" / "ocr" / "evaluation"

IMAGE_DIR = EVALUATION_DIR / "images"

IMAGE_NAMES = [
    "prescription_clean.png",
    "prescription_long_name.png",
    "prescription_multi_medications.png",
    "prescription_skewed.png",
    "prescription_damaged.png",
    "prescription_clean_jpg.jpg",
]

RESULT_DIR = EVALUATION_DIR / "results" / "rapid"

RESULT_DIR.mkdir(
    parents=True,
    exist_ok=True,
)

RAW_RESULT_PATH = RESULT_DIR / "prescription_clean_raw.json"

VIS_RESULT_PATH = RESULT_DIR / "prescription_clean_visualized.jpg"


def convert_box(box) -> list[int]:
    """
    RapidOCR의 네 꼭짓점 좌표를
    간단한 [왼쪽, 위, 오른쪽, 아래] 형태로 바꿉니다.
    """

    points = box.tolist()

    x_values = [point[0] for point in points]
    y_values = [point[1] for point in points]

    return [
        int(min(x_values)),
        int(min(y_values)),
        int(max(x_values)),
        int(max(y_values)),
    ]


def get_center(box: list[int]) -> tuple[float, float]:
    x1, y1, x2, y2 = box

    return (
        (x1 + x2) / 2,
        (y1 + y2) / 2,
    )


def make_items(
    raw_result: dict,
) -> list[dict]:
    items = []

    for text, score, box in zip(
        raw_result["rec_texts"],
        raw_result["rec_scores"],
        raw_result["rec_boxes"],
        strict=True,
    ):
        center_x, center_y = get_center(box)

        items.append(
            {
                "text": str(text).strip(),
                "confidence": float(score),
                "box": box,
                "center_x": center_x,
                "center_y": center_y,
            }
        )

    return items


def find_prescribed_date(
    items: list[dict],
) -> str | None:
    for item in items:
        match = re.fullmatch(
            r"\d{4}[-./]\d{2}[-./]\d{2}",
            item["text"],
        )

        if match:
            return item["text"].replace(".", "-").replace("/", "-")

    return None


def extract_number_and_unit(
    text: str,
) -> tuple[str | None, str | None]:
    match = re.search(
        r"(\d+(?:\.\d+)?)\s*"
        r"(정|캡슐|캡|포|봉|개|mL|ml)",
        text,
    )

    if match is None:
        return None, None

    value = match.group(1)
    unit = match.group(2)

    # '캡'은 잘린 '캡슐'로 취급하지 않고 원문 그대로 둡니다.
    return value, unit


def extract_integer(text: str) -> int | None:
    match = re.search(r"\d+", text)

    if match is None:
        return None

    return int(match.group())


def group_items_by_row(
    items: list[dict],
    tolerance: float = 25,
) -> list[list[dict]]:
    sorted_items = sorted(
        items,
        key=lambda item: item["center_y"],
    )

    rows: list[list[dict]] = []

    for item in sorted_items:
        target_row = None

        for row in rows:
            row_y = sum(row_item["center_y"] for row_item in row) / len(row)

            if abs(item["center_y"] - row_y) <= tolerance:
                target_row = row
                break

        if target_row is None:
            rows.append([item])
        else:
            target_row.append(item)

    return rows


def structure_rapid_result(  # noqa: C901
    raw_result: dict,
) -> dict:
    items = make_items(raw_result)

    dose_header = None
    frequency_header = None
    timing_header = None

    for item in items:
        header_text = re.sub(
            r"\s+",
            "",
            item["text"],
        )

        if "1회투여량" in header_text or "1회투약량" in header_text:
            dose_header = item

        elif "1일투여횟수" in header_text or "1일투약횟수" in header_text:
            frequency_header = item

        elif header_text == "용법":
            timing_header = item

    if dose_header is None:
        raise ValueError("1회 용량 열 제목을 찾지 못했습니다.")

    if frequency_header is None:
        raise ValueError("1일 횟수 열 제목을 찾지 못했습니다.")

    if timing_header is None:
        raise ValueError("복용 시점 열 제목을 찾지 못했습니다.")

    dose_x = dose_header["center_x"]
    frequency_x = frequency_header["center_x"]
    timing_x = timing_header["center_x"]

    # RapidOCR이 읽지 못한 약품명·기간 열의 위치를
    # 확인된 표 제목 위치를 기준으로 계산합니다.
    column_centers = {
        "medication_name": (dose_x - (frequency_x - dose_x) * 1.2),
        "dose": dose_x,
        "frequency": frequency_x,
        "duration": (frequency_x + timing_x) / 2,
        "timing": timing_x,
    }

    header_y = max(
        dose_header["center_y"],
        frequency_header["center_y"],
        timing_header["center_y"],
    )

    stop_words = [
        "복약",
        "지도사항",
        "주사제",
        "조제 여부",
        "조제여부",
    ]

    stop_y_candidates = [
        item["center_y"]
        for item in items
        if item["center_y"] > header_y and any(word in item["text"] for word in stop_words)
    ]

    table_bottom = min(stop_y_candidates) if stop_y_candidates else float("inf")

    table_items = [item for item in items if header_y < item["center_y"] < table_bottom]

    rows = group_items_by_row(table_items)

    medications = []

    for row in rows:
        columns = {
            "medication_name": [],
            "dose": [],
            "frequency": [],
            "duration": [],
            "timing": [],
        }

        for item in row:
            column_name = min(
                column_centers,
                key=lambda name: abs(item["center_x"] - column_centers[name]),
            )

            columns[column_name].append(item)

        dose_value = None
        dose_unit = None

        if columns["dose"]:
            dose_text = " ".join(item["text"] for item in columns["dose"])

            dose_value, dose_unit = extract_number_and_unit(dose_text)

        frequency = None

        if columns["frequency"]:
            frequency = extract_integer(columns["frequency"][0]["text"])

        duration = None

        if columns["duration"]:
            duration = extract_integer(columns["duration"][0]["text"])

        timing = None

        if columns["timing"]:
            timing = " ".join(
                item["text"]
                for item in sorted(
                    columns["timing"],
                    key=lambda value: value["center_x"],
                )
            )

        medication_name = None

        if columns["medication_name"]:
            medication_name = " ".join(
                item["text"]
                for item in sorted(
                    columns["medication_name"],
                    key=lambda value: value["center_x"],
                )
            )

        # 약품명이나 복용 정보가 일정 수준 존재하는 행만
        # 실제 약품 행 후보로 인정합니다.
        information_count = sum(
            value is not None
            for value in [
                dose_value,
                frequency,
                duration,
                timing,
            ]
        )

        if medication_name is None and information_count < 2:
            continue

        if medication_name is not None and information_count < 1:
            continue

        medications.append(
            {
                "medication_name": medication_name,
                "dose_value": dose_value,
                "dose_unit": dose_unit,
                "frequency_per_day": frequency,
                "duration_days": duration,
                "timing": timing,
            }
        )

    return {
        "engine": "rapidocr",
        "image": raw_result["image"],
        "prescribed_date": find_prescribed_date(items),
        "medications": medications,
        "structure_error": None,
    }


def run_evaluation(
    engine: RapidOCR,
    image_path: Path,
) -> None:
    print()
    print(f"RapidOCR 실행 이미지: {image_path.name}")

    result = engine(str(image_path))

    if result is None:
        raise RuntimeError("RapidOCR 결과가 없습니다.")

    texts = list(result.txts) if result.txts is not None else []

    scores = list(result.scores) if result.scores is not None else []

    boxes = list(result.boxes) if result.boxes is not None else []

    raw_result = {
        "engine": "rapidocr",
        "engine_version": version("rapidocr"),
        "runtime": {
            "name": "onnxruntime",
            "version": version("onnxruntime"),
        },
        "models": {
            "detection": "ch_PP-OCRv5_det_mobile",
            "classification": "ch_ppocr_mobile_v2.0_cls_mobile",
            "recognition": "korean_PP-OCRv5_rec_mobile",
        },
        "image": image_path.name,
        "rec_texts": [str(text) for text in texts],
        "rec_scores": [float(score) for score in scores],
        "rec_boxes": [convert_box(box) for box in boxes],
        "elapsed_seconds": float(result.elapse),
    }

    result_stem = image_path.stem

    raw_result_path = RESULT_DIR / f"{result_stem}_raw.json"

    structured_result_path = RESULT_DIR / f"{result_stem}_structured.json"

    visualized_result_path = RESULT_DIR / f"{result_stem}_visualized.jpg"

    with raw_result_path.open(
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            raw_result,
            file,
            ensure_ascii=False,
            indent=2,
        )

    try:
        structured_result = structure_rapid_result(raw_result)

    except Exception as error:
        structured_result = {
            "engine": "rapidocr",
            "image": image_path.name,
            "prescribed_date": None,
            "medications": [],
            "structure_error": str(error),
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

    print(f"구조화 결과 저장: {structured_result_path}")
    print(
        json.dumps(
            structured_result,
            ensure_ascii=False,
            indent=2,
        )
    )

    result.vis(str(visualized_result_path))

    print(f"인식한 텍스트 수: {len(texts)}개")
    print(f"처리 시간: {result.elapse:.3f}초")
    print(f"원본 결과 저장: {raw_result_path}")
    print(f"시각화 결과 저장: {visualized_result_path}")


def main() -> None:
    engine = RapidOCR(
        params={
            "Det.engine_type": EngineType.ONNXRUNTIME,
            "Det.lang_type": LangDet.CH,
            "Det.model_type": ModelType.MOBILE,
            "Det.ocr_version": OCRVersion.PPOCRV5,
            "Rec.engine_type": EngineType.ONNXRUNTIME,
            "Rec.lang_type": LangRec.KOREAN,
            "Rec.model_type": ModelType.MOBILE,
            "Rec.ocr_version": OCRVersion.PPOCRV5,
        }
    )

    for image_name in IMAGE_NAMES:
        image_path = IMAGE_DIR / image_name

        if not image_path.exists():
            print(f"이미지를 찾을 수 없어 건너뜁니다: {image_path}")
            continue

        try:
            run_evaluation(
                engine=engine,
                image_path=image_path,
            )

        except Exception as error:
            print(f"{image_name} 실행 실패: {type(error).__name__}: {error}")


if __name__ == "__main__":
    main()
