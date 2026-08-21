import json
import re
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EVALUATION_DIR = PROJECT_ROOT / "tests" / "fixtures" / "ocr" / "evaluation"
EXPECTED_DIR = EVALUATION_DIR / "expected"
RESULT_DIR = EVALUATION_DIR / "results"
OUTPUT_PATH = PROJECT_ROOT / "tests" / "evals" / "ocr" / "ocr_metrics.json"

MEDICATION_FIELDS = (
    "medication_name",
    "dose_value",
    "dose_unit",
    "frequency_per_day",
    "duration_days",
    "timing",
)

CLOVA_FIELD_MAP = {
    "MEDICATION_NAME": "medication_name",
    "DOSE_VALUE": "dose_value",
    "DOSE_UNIT": "dose_unit",
    "FREQUENCY_PER_DAY": "frequency_per_day",
    "DURATION_DAYS": "duration_days",
    "TIMING": "timing",
}

BASELINE_STEMS = (
    "prescription_clean",
    "prescription_long_name",
    "prescription_multi_medications",
    "prescription_skewed",
    "prescription_damaged",
)

FORMAT_STEMS = (
    "prescription_clean",
    "prescription_clean_jpg",
)


def normalize(value: Any) -> str | None:
    """평가에서 의미가 없는 공백 차이를 제거합니다."""

    if value is None:
        return None

    return re.sub(r"\s+", "", str(value)).strip()


def normalize_date(value: Any) -> str | None:
    """날짜의 점·슬래시·하이픈 표기를 같은 값으로 봅니다."""

    normalized = normalize(value)

    if normalized is None:
        return None

    return normalized.replace(".", "-").replace("/", "-")


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def load_expected(stem: str) -> dict[str, Any]:
    return load_json(EXPECTED_DIR / f"{stem}.expected.json")


def load_clova(stem: str) -> dict[str, Any]:
    result = load_json(RESULT_DIR / "clova" / f"{stem}.result.json")["data"]

    prescribed_date = None
    medications: dict[int, dict[str, Any]] = {}

    for field in result.get("fields", []):
        field_type = field.get("field_type")
        raw_value = field.get("raw_value")

        if field_type == "PRESCRIBED_DATE":
            prescribed_date = raw_value
            continue

        normalized_name = CLOVA_FIELD_MAP.get(field_type)
        medication_index = field.get("medication_index")

        if normalized_name is None or not medication_index:
            continue

        medication = medications.setdefault(
            int(medication_index),
            {},
        )
        medication[normalized_name] = raw_value

    return {
        "prescribed_date": prescribed_date,
        "medications": [medications[index] for index in sorted(medications)],
    }


def load_local_engine(engine: str, stem: str) -> dict[str, Any]:
    return load_json(RESULT_DIR / engine / f"{stem}_structured.json")


def load_actual(engine: str, stem: str) -> dict[str, Any]:
    if engine == "clova":
        return load_clova(stem)

    return load_local_engine(engine, stem)


def evaluate_image(
    expected: dict[str, Any],
    actual: dict[str, Any],
) -> dict[str, Any]:
    expected_medications = expected.get("medications", [])
    actual_medications = actual.get("medications", [])

    matched_fields = 0
    missing_or_mismatched_fields = 0
    false_positive_fields = 0

    if normalize_date(expected.get("prescribed_date")) == normalize_date(actual.get("prescribed_date")):
        matched_fields += 1
    else:
        missing_or_mismatched_fields += 1
        if actual.get("prescribed_date") is not None:
            false_positive_fields += 1

    exact_medication_rows = 0

    for index, expected_medication in enumerate(expected_medications):
        actual_medication = actual_medications[index] if index < len(actual_medications) else {}

        row_is_exact = True

        for field_name in MEDICATION_FIELDS:
            expected_value = expected_medication.get(field_name)
            actual_value = actual_medication.get(field_name)

            if normalize(expected_value) == normalize(actual_value):
                matched_fields += 1
                continue

            row_is_exact = False
            missing_or_mismatched_fields += 1

            if actual_value is not None:
                false_positive_fields += 1

        if row_is_exact:
            exact_medication_rows += 1

    for extra_medication in actual_medications[len(expected_medications) :]:
        false_positive_fields += sum(extra_medication.get(field_name) is not None for field_name in MEDICATION_FIELDS)

    expected_fields = 1 + len(expected_medications) * len(MEDICATION_FIELDS)
    precision_denominator = matched_fields + false_positive_fields

    recall = round(
        matched_fields / expected_fields * 100,
        1,
    )
    precision = (
        round(
            matched_fields / precision_denominator * 100,
            1,
        )
        if precision_denominator
        else None
    )
    row_exact_rate = (
        round(
            exact_medication_rows / len(expected_medications) * 100,
            1,
        )
        if expected_medications
        else None
    )

    return {
        "expected_fields": expected_fields,
        "matched_fields": matched_fields,
        "missing_or_mismatched_fields": (missing_or_mismatched_fields),
        "false_positive_fields": false_positive_fields,
        "recall_percent": recall,
        "precision_percent": precision,
        "expected_medication_rows": len(expected_medications),
        "exact_medication_rows": exact_medication_rows,
        "medication_row_exact_rate_percent": row_exact_rate,
        "actual_medication_rows": len(actual_medications),
        "structure_error": actual.get("structure_error"),
    }


def aggregate(
    image_results: dict[str, dict[str, Any]],
    stems: tuple[str, ...],
) -> dict[str, Any]:
    selected = [image_results[stem] for stem in stems]

    expected_fields = sum(result["expected_fields"] for result in selected)
    matched_fields = sum(result["matched_fields"] for result in selected)
    missing = sum(result["missing_or_mismatched_fields"] for result in selected)
    false_positives = sum(result["false_positive_fields"] for result in selected)
    expected_rows = sum(result["expected_medication_rows"] for result in selected)
    exact_rows = sum(result["exact_medication_rows"] for result in selected)
    precision_denominator = matched_fields + false_positives

    return {
        "expected_fields": expected_fields,
        "matched_fields": matched_fields,
        "missing_or_mismatched_fields": missing,
        "false_positive_fields": false_positives,
        "recall_percent": round(
            matched_fields / expected_fields * 100,
            1,
        ),
        "precision_percent": (
            round(
                matched_fields / precision_denominator * 100,
                1,
            )
            if precision_denominator
            else None
        ),
        "expected_medication_rows": expected_rows,
        "exact_medication_rows": exact_rows,
        "medication_row_exact_rate_percent": round(
            exact_rows / expected_rows * 100,
            1,
        ),
    }


def main() -> None:
    output: dict[str, Any] = {
        "evaluation_rules": {
            "normalization": "모든 공백을 제거한 뒤 비교",
            "mismatch": ("정답 기준 불일치 1건과 출력 기준 오탐 1건으로 각각 계산"),
            "baseline_images": list(BASELINE_STEMS),
            "pdf_included": False,
        },
        "engines": {},
    }

    for engine in ("clova", "paddle", "rapid"):
        image_results: dict[str, dict[str, Any]] = {}

        for stem in (*BASELINE_STEMS, "prescription_clean_jpg"):
            expected = load_expected(stem)
            actual = load_actual(engine, stem)
            image_results[stem] = evaluate_image(
                expected,
                actual,
            )

        output["engines"][engine] = {
            "images": image_results,
            "baseline_total": aggregate(
                image_results,
                BASELINE_STEMS,
            ),
            "format_comparison": {stem: image_results[stem] for stem in FORMAT_STEMS},
        }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    with OUTPUT_PATH.open("w", encoding="utf-8") as file:
        json.dump(
            output,
            file,
            ensure_ascii=False,
            indent=2,
        )

    print(f"평가 결과 저장: {OUTPUT_PATH}")

    for engine, result in output["engines"].items():
        total = result["baseline_total"]
        print(
            f"{engine}: "
            f"재현율 {total['recall_percent']}%, "
            f"정밀도 {total['precision_percent']}%, "
            "약품 행 완전 일치율 "
            f"{total['medication_row_exact_rate_percent']}%"
        )


if __name__ == "__main__":
    main()
