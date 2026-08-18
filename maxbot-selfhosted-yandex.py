#!/usr/bin/env python3
"""Yandex production wrapper around the shared MAX bot core.

The shared self-hosted implementation remains the source for business flows. This
wrapper applies production-only compatibility/acceptance overrides without changing
the standalone VPS image.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

CORE_PATH = Path(__file__).resolve().with_name("maxbot-selfhosted-core.py")

_spec = importlib.util.spec_from_file_location("maximum_maxbot_shared_core", CORE_PATH)
if _spec is None or _spec.loader is None:
    raise RuntimeError(f"Cannot load shared MAX bot core: {CORE_PATH}")
_core = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_core)


def _finish_lead_exact(chat_id, user_id, kind, data, phone, verified):
    """Persist the lead and use the exact acceptance text from project standard."""
    lead_id = _core.save_lead(chat_id, user_id, kind, data, phone, verified)
    _core.clear_session(chat_id)
    _core.send_message(
        chat_id,
        "Спасибо! Заявка принята ✅ Катерина свяжется с Вами и уточнит детали.",
        buttons=_core.BACK_BUTTONS,
    )

    if _core.ADMIN_CHAT_ID:
        detail_lines = [f"Новая заявка #{lead_id}", f"Тип: {kind}"]
        if data.get("name"):
            detail_lines.append(f"Имя: {data['name']}")
        if data.get("city"):
            detail_lines.append(f"Город: {data['city']}")
        detail_lines.append(f"Телефон: {phone or 'не указан'}")
        for key, value in data.items():
            if key not in {"name", "city", "phone"} and value:
                detail_lines.append(f"{key}: {value}")
        try:
            _core.send_message(_core.ADMIN_CHAT_ID, "\n".join(detail_lines)[:3900])
        except Exception as exc:
            print("admin notify error:", repr(exc), flush=True)


# Functions defined in the original module resolve globals in that module. Patch the
# original module itself first, then re-export it so all existing flows use the exact
# acceptance implementation above.
_core.finish_lead = _finish_lead_exact

globals().update(
    {
        name: value
        for name, value in vars(_core).items()
        if name not in {"__name__", "__file__", "__package__", "__loader__", "__spec__"}
    }
)
finish_lead = _finish_lead_exact
