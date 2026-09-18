"""Synthetic image and transaction boundary regressions for #809."""

from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from PIL import Image
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from starlette.datastructures import Headers, UploadFile

from ai_worker.adapters.sqlalchemy_ocr_input_repository import SqlAlchemyOcrInputRepository
from app.core import config
from app.core.errors import ApiError
from app.dtos.medical_documents import MedicalDocumentType
from app.repositories.account_deletion_request_repository import AccountDeletionRequestRepository
from app.repositories.medical_document_repository import MedicalDocumentRepository
from app.services import document_image_normalizer as normalizer
from app.services.medical_documents import MedicalDocumentService
from app.services.ocr import _to_job_data


def image_bytes(orientation=1, fmt="PNG"):
    image = Image.new("RGB", (3, 2))
    image.putdata([(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0), (0, 255, 255), (255, 0, 255)])
    exif = Image.Exif()
    exif[274] = orientation
    exif[270] = "synthetic private metadata"
    output = BytesIO()
    image.save(output, format=fmt, exif=exif)
    return output.getvalue()


@pytest.mark.parametrize(
    "orientation,operation",
    [
        (1, None),
        (2, Image.Transpose.FLIP_LEFT_RIGHT),
        (3, Image.Transpose.ROTATE_180),
        (4, Image.Transpose.FLIP_TOP_BOTTOM),
        (5, Image.Transpose.TRANSPOSE),
        (6, Image.Transpose.ROTATE_270),
        (7, Image.Transpose.TRANSVERSE),
        (8, Image.Transpose.ROTATE_90),
    ],
)
def test_exif_pixels_dimensions_and_metadata(orientation, operation):
    original = image_bytes(orientation)
    result = normalizer.normalize_document_image(original, "image/png")
    source = Image.open(BytesIO(original)).convert("RGB")
    expected = source.transpose(operation) if operation is not None else source
    output = Image.open(BytesIO(result.content))
    assert output.size == expected.size == (result.width, result.height)
    assert output.tobytes() == expected.tobytes()
    assert not output.getexif() and not output.info
    assert Image.open(BytesIO(original)).getexif()[274] == orientation


def test_jpeg_becomes_metadata_free_png():
    result = normalizer.normalize_document_image(image_bytes(6, "JPEG"), "image/jpeg")
    assert (result.width, result.height) == (2, 3)
    assert Image.open(BytesIO(result.content)).format == "PNG"


@pytest.mark.parametrize(
    "content,mime",
    [(b"\xff\xd8\xff fake", "image/jpeg"), (b"\x89PNG\r\n\x1a\n", "image/png"), (image_bytes(), "image/jpeg")],
)
def test_invalid_decode_and_format_fail_closed(content, mime):
    with pytest.raises(ApiError) as error:
        normalizer.normalize_document_image(content, mime)
    assert error.value.code == "UPLOAD_FILE_INVALID_TYPE"
    assert "fake" not in error.value.message


def test_pixel_and_output_limits(monkeypatch):
    monkeypatch.setattr(normalizer, "MAX_IMAGE_PIXELS", 5)
    with pytest.raises(ApiError):
        normalizer.normalize_document_image(image_bytes(), "image/png")
    monkeypatch.setattr(normalizer, "MAX_IMAGE_PIXELS", 20_000_000)
    monkeypatch.setattr(normalizer, "MAX_NORMALIZED_BYTES", 1)
    with pytest.raises(ApiError):
        normalizer.normalize_document_image(image_bytes(), "image/png")


def test_bomb_warning_rejected(monkeypatch):
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 4)
    with pytest.raises(ApiError):
        normalizer.normalize_document_image(image_bytes(), "image/png")


def test_animated_png_rejected():
    output = BytesIO()
    Image.new("RGB", (2, 2), "red").save(
        output, format="PNG", save_all=True, append_images=[Image.new("RGB", (2, 2), "blue")]
    )
    with pytest.raises(ApiError):
        normalizer.normalize_document_image(output.getvalue(), "image/png")


@pytest.mark.parametrize("action,exists", [("commit", True), ("rollback", False), ("close", False)])
def test_transaction_cleanup(tmp_path, action, exists):
    session = Session(create_engine("sqlite://"))
    session.execute(text("SELECT 1"))
    paths = [tmp_path / "original", tmp_path / "normalized"]
    for p in paths:
        p.write_bytes(b"synthetic")
    repository = MedicalDocumentRepository(SimpleNamespace(sync_session=session))
    repository.track_uploaded_files(paths)
    getattr(session, action)()
    assert all(p.exists() == exists for p in paths)
    session.close()


def test_savepoint_commit_does_not_keep_files_after_outer_rollback(tmp_path):
    session = Session(create_engine("sqlite://"))
    session.begin()
    path = tmp_path / "normalized"
    path.write_bytes(b"synthetic")
    MedicalDocumentRepository(SimpleNamespace(sync_session=session)).track_uploaded_files([path])
    with session.begin_nested():
        session.execute(text("SELECT 1"))
    session.rollback()
    assert not path.exists()


class UploadRepository:
    def __init__(self, fail=False):
        self.document = SimpleNamespace(
            id=uuid4(),
            upload_status="UPLOADED",
            uploaded_at=datetime.now(UTC),
            normalized_object_key=None,
            normalized_width=None,
            normalized_height=None,
        )
        self.fail = fail

    async def create(self, **kwargs):
        self.document.__dict__.update(kwargs)
        return self.document

    def track_uploaded_files(self, paths):
        self.paths = paths

    async def update_object_key(self, document, key):
        if self.fail:
            raise RuntimeError("simulated flush failure")
        document.object_key = key

    async def get_owned(self, **kwargs):
        return self.document


@pytest.mark.parametrize("pdf", [False, True])
async def test_upload_preserves_original_and_viewer_asset(tmp_path, monkeypatch, pdf):
    monkeypatch.setattr(config, "STORAGE_DIR", str(tmp_path))
    content = b"%PDF-1.7 synthetic" if pdf else image_bytes(6)
    repository = UploadRepository()
    service = MedicalDocumentService(repository)
    upload = UploadFile(
        BytesIO(content),
        filename="x.pdf" if pdf else "x.png",
        headers=Headers({"content-type": "application/pdf" if pdf else "image/png"}),
    )
    await service.create_prescription_document(
        user=SimpleNamespace(id=uuid4()), file=upload, document_type=MedicalDocumentType.PRESCRIPTION
    )
    doc = repository.document
    assert (tmp_path / doc.object_key).read_bytes() == content
    if pdf:
        assert doc.normalized_object_key is None
        with pytest.raises(ApiError):
            await service.get_normalized_document_file(user=None, document_id=doc.id)
    else:
        result = await service.get_normalized_document_file(user=None, document_id=doc.id)
        assert Path(result.file_path).read_bytes() == normalizer.normalize_document_image(content, "image/png").content
        assert (doc.normalized_width, doc.normalized_height) == (2, 3)


async def test_upload_failure_removes_both_files(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STORAGE_DIR", str(tmp_path))
    service = MedicalDocumentService(UploadRepository(fail=True))
    upload = UploadFile(BytesIO(image_bytes()), filename="x.png", headers=Headers({"content-type": "image/png"}))
    with pytest.raises(RuntimeError):
        await service.create_prescription_document(
            user=None, file=upload, document_type=MedicalDocumentType.PRESCRIPTION
        )
    assert list(tmp_path.iterdir()) == []


async def test_viewer_ownership_and_path_boundary(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STORAGE_DIR", str(tmp_path))
    repository = UploadRepository()
    repository.get_owned = AsyncMock(return_value=None)
    service = MedicalDocumentService(repository)
    with pytest.raises(ApiError) as error:
        await service.get_normalized_document_file(user=None, document_id=uuid4())
    assert error.value.status_code == 404
    repository.get_owned = AsyncMock(
        return_value=SimpleNamespace(normalized_object_key="../outside", normalized_width=2, normalized_height=2)
    )
    with pytest.raises(ApiError):
        await service.get_normalized_document_file(user=None, document_id=uuid4())


@pytest.mark.parametrize("normalized", [False, True])
def test_result_disables_legacy_highlight(normalized):
    document = SimpleNamespace(
        normalized_object_key="normalized.png" if normalized else None,
        normalized_width=3 if normalized else None,
        normalized_height=2 if normalized else None,
    )
    job = SimpleNamespace(
        document=document,
        id=uuid4(),
        document_id=uuid4(),
        ocr_status="COMPLETED",
        error_code=None,
        error_message=None,
        engine_name="test",
        model_version=None,
        prompt_version=None,
        llm_processing=None,
        created_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
    )
    field = SimpleNamespace(
        id=uuid4(),
        field_type="MEDICATION_NAME",
        medication_index=1,
        raw_value="synthetic",
        normalized_value=None,
        confirmed_value=None,
        confidence_score=None,
        confirmation_status="UNCONFIRMED",
        normalization_version=None,
        source_page=1,
        source_bbox_x=0,
        source_bbox_y=0,
        source_bbox_width=2,
        source_bbox_height=1,
    )
    result = _to_job_data(job, [field])
    assert result.source_image.normalized is normalized
    assert (result.fields[0].source_location is not None) is normalized
    assert (result.source_image.url is not None) is normalized


async def test_worker_input_and_withdrawal_use_both_assets():
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE medical_document (id TEXT, uploaded_by TEXT, object_key TEXT, normalized_object_key TEXT, file_mime_type TEXT)"
            )
        )
        connection.execute(text("CREATE TABLE ocr_job (id TEXT, document_id TEXT, ai_job_id TEXT)"))
        user, doc, job, ai = map(str, [uuid4(), uuid4(), uuid4(), uuid4()])
        connection.execute(
            text("INSERT INTO medical_document VALUES (:doc,:user,'original.jpg','normalized.png','image/jpeg')"),
            dict(doc=doc, user=user),
        )
        connection.execute(text("INSERT INTO ocr_job VALUES (:job,:doc,:ai)"), dict(job=job, doc=doc, ai=ai))

        class Adapter:
            async def execute(self, statement, params=None):
                return connection.execute(statement, params or {})

        result = await SqlAlchemyOcrInputRepository(Adapter()).get_input(domain_id=job, job_id=ai)
        assert (result.object_key, result.file_mime_type) == ("normalized.png", "image/png")
        assert set(await AccountDeletionRequestRepository(Adapter())._list_medical_document_object_keys(user)) == {
            "original.jpg",
            "normalized.png",
        }
        connection.execute(text("UPDATE medical_document SET normalized_object_key=NULL"))
        result = await SqlAlchemyOcrInputRepository(Adapter()).get_input(domain_id=job, job_id=ai)
        assert (result.object_key, result.file_mime_type) == ("original.jpg", "image/jpeg")
