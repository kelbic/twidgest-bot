"""Тонкий клиент API ЮKassa (api.yookassa.ru/v3) на aiohttp.

Нужен для способов оплаты, которых нет в нативной телеграм-форме, — в
первую очередь СБП. Авторизация — HTTP Basic shopId:secretKey (секретный
ключ из ЛК, раздел «Ключи API»). Никакого SDK: на прод-VPS pip недоступен,
aiohttp уже есть транзитивно от aiogram.
"""
from __future__ import annotations

import uuid
from typing import Any

import aiohttp

API_BASE = "https://api.yookassa.ru/v3"


class YooKassaError(RuntimeError):
    """Ошибка API ЮKassa: HTTP-статус + тело ответа (там код и описание)."""


def build_sbp_payment_body(
    amount_rub: int,
    description: str,
    return_url: str,
    metadata: dict[str, str],
    receipt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Тело POST /payments для СБП-платежа. Вынесено ради тестируемости.

    capture=true — одностадийный платёж: деньги списываются сразу, без
    ручного подтверждения. payment_method_data=sbp ведёт плательщика
    сразу в выбор банка, минуя общую форму.
    """
    body: dict[str, Any] = {
        "amount": {"value": f"{amount_rub}.00", "currency": "RUB"},
        "capture": True,
        "payment_method_data": {"type": "sbp"},
        "confirmation": {"type": "redirect", "return_url": return_url},
        "description": description[:128],
        "metadata": metadata,
    }
    if receipt is not None:
        body["receipt"] = receipt
    return body


class YooKassaClient:
    def __init__(self, shop_id: str, secret_key: str) -> None:
        self._auth = aiohttp.BasicAuth(shop_id, secret_key)

    async def _request(
        self,
        method: str,
        path: str,
        json_body: dict[str, Any] | None = None,
        idempotence_key: str | None = None,
    ) -> dict[str, Any]:
        headers = {}
        if idempotence_key:
            headers["Idempotence-Key"] = idempotence_key
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.request(
                method, f"{API_BASE}{path}",
                json=json_body, auth=self._auth, headers=headers,
            ) as resp:
                text = await resp.text()
                if resp.status >= 400:
                    raise YooKassaError(f"HTTP {resp.status}: {text[:500]}")
                return await resp.json(content_type=None)

    async def me(self) -> dict[str, Any]:
        """Настройки магазина: статус, способы оплаты, фискализация.

        Это и есть самопроверка ключа: 401 = ключ битый/отозван.
        """
        return await self._request("GET", "/me")

    async def create_sbp_payment(
        self,
        amount_rub: int,
        description: str,
        return_url: str,
        metadata: dict[str, str],
        receipt: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Создаёт СБП-платёж, возвращает объект платежа ЮKassa.

        Ссылка для плательщика — в confirmation.confirmation_url.
        Idempotence-Key на каждый вызов свой: повторное нажатие кнопки —
        это НОВЫЙ платёж (старый никто не оплатил), а не ретрай.
        """
        body = build_sbp_payment_body(
            amount_rub, description, return_url, metadata, receipt
        )
        return await self._request(
            "POST", "/payments", json_body=body,
            idempotence_key=str(uuid.uuid4()),
        )

    async def get_payment(self, payment_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/payments/{payment_id}")
