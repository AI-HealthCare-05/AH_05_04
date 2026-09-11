"""Two-pass product identity validation retaining locations, never raw values."""

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from ai_worker.tasks.rag.source_ingestion.reject_codes import ProductRejectCode


@dataclass(frozen=True, slots=True)
class ProductRejection:
    page_number: int
    record_index: int
    code: ProductRejectCode

    @property
    def parser_location(self) -> str:
        return f"page[{self.page_number}].record[{self.record_index}]"


class ProductIdentityError(ValueError):
    def __init__(self, rejections: tuple[ProductRejection, ...]) -> None:
        self.rejections = rejections
        super().__init__("Product identity validation failed.")


def classify_product_rejections(
    pages: Iterable[tuple[int, Iterable[Mapping[str, object]]]],
) -> tuple[ProductRejection, ...]:
    pending: list[tuple[int, int, str]] = []
    rejected: list[ProductRejection] = []
    seen_pages: set[int] = set()
    counts: Counter[str] = Counter()
    for page_number, records in pages:
        if type(page_number) is not int or page_number < 1 or page_number in seen_pages:
            raise ValueError("Product page binding is invalid.")
        seen_pages.add(page_number)
        for index, record in enumerate(records):
            value = record.get("ITEM_SEQ")
            if value is None or (isinstance(value, str) and not value.strip()):
                rejected.append(ProductRejection(page_number, index, ProductRejectCode.ITEM_SEQ_REQUIRED))
            elif not isinstance(value, str):
                rejected.append(ProductRejection(page_number, index, ProductRejectCode.INVALID_ITEM_SEQ_TYPE))
            else:
                pending.append((page_number, index, value))
                counts[value] += 1
    for page_number, index, value in pending:
        if counts[value] > 1:
            rejected.append(ProductRejection(page_number, index, ProductRejectCode.DUPLICATE_ITEM_SEQ))
    return tuple(sorted(rejected, key=lambda item: (item.page_number, item.record_index)))
