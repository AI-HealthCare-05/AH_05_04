from fastapi import APIRouter

from app.apis.v1.auth_routers import auth_router
from app.apis.v1.chat_routers import chat_router
from app.apis.v1.extracted_field_routers import extracted_field_router
from app.apis.v1.guide_routers import guide_router
from app.apis.v1.medical_document_routers import medical_document_router
from app.apis.v1.ocr_routers import ocr_router
from app.apis.v1.prescription_routers import prescription_router
from app.apis.v1.user_routers import user_router

v1_routers = APIRouter(prefix="/api/v1")
v1_routers.include_router(auth_router)
v1_routers.include_router(user_router)
v1_routers.include_router(medical_document_router)
v1_routers.include_router(ocr_router)
v1_routers.include_router(extracted_field_router)
v1_routers.include_router(prescription_router)
v1_routers.include_router(guide_router)
v1_routers.include_router(chat_router)
