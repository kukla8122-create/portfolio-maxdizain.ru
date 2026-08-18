#!/usr/bin/env python3
"""Yandex production wrapper around the shared MAX bot core.

The shared self-hosted implementation remains the source for business flows. This
wrapper applies production-only compatibility/acceptance and trust-boundary
controls without changing the standalone VPS image.

Production policy for «МАКСимум мебель»:
- private dialog (recipient.chat_type == "dialog") -> client menu, FAQ and leads;
- group chat / channel -> no client business flow and no public bot replies.

MAX's current official SDK schema exposes Recipient.chat_type with values
"dialog", "chat" and "channel". We intentionally fail closed if that field is
missing on message_created/message_callback: losing one automated reply is safer
than leaking a lead flow into a public/group context.

Yandex adapters patch this wrapper module with YDB-backed storage and hardened
contact parsing after import. Functions inherited from the shared module still
resolve their globals inside that shared module, so the wrapper synchronizes the
runtime bindings into the underlying module before every Update is processed.
This prevents sessions, channels or leads from silently falling back to container-
local SQLite after the YDB adapter has been installed.
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


_RUNTIME_BINDINGS = (
    "init_db",
    "set_session",
    "get_session",
    "clear_session",
    "save_channel",
    "save_lead",
    "parse_contact_attachment",
)


def _sync_runtime_bindings() -> None:
    """Mirror adapter-installed callables into the shared module's globals.

    maxbot-yandex.py installs YDB/contact overrides on this wrapper object. The
    shared business-flow functions were defined in ``_core`` and therefore look up
    names such as ``get_session`` and ``save_lead`` in ``_core.__dict__``. Keeping
    the two namespaces synchronized is required for persistent YDB behavior.
    """

    namespace = globals()
    for name in _RUNTIME_BINDINGS:
        value = namespace.get(name)
        if callable(value):
            setattr(_core, name, value)


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


def _recipient_chat_type(update) -> str:
    """Return MAX Recipient.chat_type for the message carried by an Update."""
    message = _core.extract_message(update)
    recipient = message.get("recipient") or {}
    return str(recipient.get("chat_type") or "").strip().lower()


def _is_private_dialog(update) -> bool:
    return _recipient_chat_type(update) == "dialog"


def _ack_callback_without_business_action(update) -> None:
    callback = update.get("callback") or {}
    callback_id = callback.get("callback_id")
    if not callback_id:
        return
    try:
        _core.answer_callback(callback_id)
    except Exception as exc:
        # Keep the same resilience policy as the shared callback handler: a failed
        # acknowledgement must not make us run a public/group business flow.
        print("callback answer error:", repr(exc), flush=True)


_original_handle_update = _core.handle_update


def _handle_update_private_dialog_only(update):
    # YDB/contact adapters are installed on this wrapper after import. Synchronize
    # their current callables before any shared-core business function is entered.
    _sync_runtime_bindings()

    update_type = update.get("update_type")
    if update_type == "message_created" and not _is_private_dialog(update):
        print(
            "message_created ignored outside private dialog",
            _core.extract_chat_id(update),
            _recipient_chat_type(update) or "missing-chat-type",
            flush=True,
        )
        return

    if update_type == "message_callback" and not _is_private_dialog(update):
        # MAX clients expect POST /answers after a callback. Acknowledge the button
        # so it does not keep spinning, but never execute menu/lead actions publicly.
        _ack_callback_without_business_action(update)
        print(
            "message_callback ignored outside private dialog",
            _core.extract_chat_id(update),
            _recipient_chat_type(update) or "missing-chat-type",
            flush=True,
        )
        return

    return _original_handle_update(update)


# Functions defined in the original module resolve globals in that module. Patch the
# original module itself first, then re-export it so all existing flows use the exact
# production behavior above.
_core.finish_lead = _finish_lead_exact
_core.handle_update = _handle_update_private_dialog_only

globals().update(
    {
        name: value
        for name, value in vars(_core).items()
        if name not in {"__name__", "__file__", "__package__", "__loader__", "__spec__"}
    }
)
finish_lead = _finish_lead_exact
recipient_chat_type = _recipient_chat_type
is_private_dialog = _is_private_dialog
sync_runtime_bindings = _sync_runtime_bindings
handle_update = _handle_update_private_dialog_only
