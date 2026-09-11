from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.database.models import User
from app.database.session import async_session_factory
from app.service.credential_service import CredentialService, _mask_key
from app.utils.encryption import decrypt_secret, encrypt_secret


def test_encrypt_decrypt_round_trip():
    secret = "sk-test-1234567890"
    ciphertext = encrypt_secret(secret, purpose="byok")
    assert ciphertext != secret
    assert decrypt_secret(ciphertext, purpose="byok") == secret


def test_mask_key_shows_only_first_and_last_four_chars():
    masked = _mask_key("sk-abcdefghij1234")
    assert masked.startswith("sk-a")
    assert masked.endswith("1234")
    assert "abcdefghij" not in masked


@pytest.fixture(autouse=True)
async def cleanup():
    yield
    async with async_session_factory() as session:
        result = await session.execute(
            select(User).where(User.email.like("test-credentials%"))
        )
        for user in result.scalars().all():
            await session.delete(user)
        await session.commit()


@pytest.mark.asyncio
async def test_save_list_and_delete_credential():
    async with async_session_factory() as session:
        user = User(
            email="test-credentials@example.com",
            username="test_credentials",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)

        with patch(
            "app.service.credential_service.CredentialValidationService.validate",
            return_value=None,
        ):
            saved = await CredentialService.save_credential(
                session,
                user_id=user.id,
                provider_type="openai_llm",
                api_key="sk-test-1234567890",
            )

        assert saved.provider_type == "openai_llm"

        credentials = await CredentialService.list_credentials(session, user_id=user.id)
        assert len(credentials) == 1
        preview = CredentialService.masked_preview(credentials[0])
        assert preview.startswith("sk-t")
        assert preview.endswith("7890")
        assert "1234567890" not in preview

        await CredentialService.delete_credential(
            session, user_id=user.id, provider_type="openai_llm"
        )
        remaining = await CredentialService.list_credentials(session, user_id=user.id)
        assert remaining == []


@pytest.mark.asyncio
async def test_saving_again_for_same_provider_overwrites_not_duplicates():
    async with async_session_factory() as session:
        user = User(
            email="test-credentials-2@example.com",
            username="test_credentials_2",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)

        with patch(
            "app.service.credential_service.CredentialValidationService.validate",
            return_value=None,
        ):
            await CredentialService.save_credential(
                session,
                user_id=user.id,
                provider_type="openai_llm",
                api_key="sk-old-key-0000",
            )
            await CredentialService.save_credential(
                session,
                user_id=user.id,
                provider_type="openai_llm",
                api_key="sk-new-key-1111",
            )

        credentials = await CredentialService.list_credentials(session, user_id=user.id)
        assert len(credentials) == 1
        assert decrypt_secret(credentials[0].encrypted_credential, purpose="byok") == (
            "sk-new-key-1111"
        )
