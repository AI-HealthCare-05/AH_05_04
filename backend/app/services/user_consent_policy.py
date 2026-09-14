from app.core import config
from app.models.user_consents import ConsentPurpose

STATIC_CONSENT_POLICY_VERSIONS: dict[ConsentPurpose, str] = {
    ConsentPurpose.GUIDE: "guide-consent.v1",
    ConsentPurpose.CHAT: "chat-consent.v1",
    ConsentPurpose.NOTIFICATION: "notification-consent.v1",
}


def current_consent_policy_version(purpose: ConsentPurpose) -> str:
    if purpose == ConsentPurpose.OCR:
        return config.OCR_CONSENT_POLICY_VERSION
    return STATIC_CONSENT_POLICY_VERSIONS[purpose]
