from app.models.user_consents import ConsentPurpose

CURRENT_CONSENT_POLICY_VERSIONS: dict[ConsentPurpose, str] = {
    ConsentPurpose.OCR: "ocr-consent.v1",
    ConsentPurpose.GUIDE: "guide-consent.v1",
    ConsentPurpose.CHAT: "chat-consent.v1",
    ConsentPurpose.NOTIFICATION: "notification-consent.v1",
}


def current_consent_policy_version(purpose: ConsentPurpose) -> str:
    return CURRENT_CONSENT_POLICY_VERSIONS[purpose]
