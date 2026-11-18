"""Tests for JD Smart authentication configuration flows."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.jd_smart.api import parse_curl_command
from custom_components.jd_smart.config_flow import (
    JdSmartAcConfigFlow,
    _apply_curl,
    _clean_input,
    _require_auth_fields,
)
from custom_components.jd_smart.const import (
    CONF_APP_VERSION,
    CONF_COOKIE,
    CONF_CURL,
    CONF_DEVICE_ID,
    CONF_DEVICE_MODEL,
    CONF_FEED_ID,
    CONF_PLATFORM,
    CONF_PIN,
    CONF_PLATFORM_VERSION,
    CONF_SGM_CONTEXT,
    CONF_TGT,
    CONF_USER_AGENT,
    DOMAIN,
    auth_refresh_notification_ids,
)


async def test_manual_auth_update_clears_retry_notification(hass) -> None:
    """A successful manual auth update clears the background retry notice."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id="entry-id",
        data={
            CONF_COOKIE: "old-cookie",
            CONF_TGT: "old-tgt",
            CONF_FEED_ID: "feed-id",
        },
    )
    entry.add_to_hass(hass)
    flow = JdSmartAcConfigFlow()
    flow.hass = hass

    with (
        patch.object(flow, "_async_current_entries", return_value=[entry]),
        patch.object(
            hass.config_entries,
            "async_reload",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "custom_components.jd_smart.config_flow.persistent_notification.async_dismiss"
        ) as dismiss_notification,
    ):
        await flow._async_update_auth_entries(
            {CONF_COOKIE: "new-cookie", CONF_TGT: "new-tgt"}
        )

    assert dismiss_notification.call_args_list == [
        ((hass, notification_id), {})
        for notification_id in auth_refresh_notification_ids("entry-id", ("feed-id",))
    ]


CURL_SAMPLE = (
    'curl -k -X POST -H "appplatformversion: 27.2" '
    '-H "Sgm-Context: 111;111;false;TESTFAKEKEY@0fakeFakeFakeFake0" '
    '-H "tgt: TGT-VALUE" '
    '-H "Authorization: smart key:::sig:::2026-11-18T20:58:48.808Z" '
    '-H "User-Agent: ios" '
    '-H "Cookie: pt_key=app_openKEY; pt_pin=someone; sid=abc123" '
    '-H "appplatform: iPhone14,3" '
    '-H "appversion: 2.5.0" '
    '-d "{\"json\":\"{}\"}" '
    '"https://api.smart.jd.com/c/service/integration/v1/controlDevice_v1'
    '?plat=iPhone&hard_platform=iPhone14,3&app_version=2.5.0'
    '&plat_version=27.2&device_id=DEVICE-ID-1&channel=AppStore" --compressed'
)


def test_parse_curl_extracts_every_stored_field() -> None:
    """A pasted curl command yields all credentials the integration stores."""
    parsed = parse_curl_command(CURL_SAMPLE)

    assert parsed[CONF_TGT] == "TGT-VALUE"
    assert parsed[CONF_COOKIE] == "pt_key=app_openKEY; pt_pin=someone; sid=abc123"
    assert parsed[CONF_SGM_CONTEXT] == "111;111;false;TESTFAKEKEY@0fakeFakeFakeFake0"
    assert parsed[CONF_USER_AGENT] == "ios"
    assert parsed[CONF_PLATFORM] == "iPhone"
    assert parsed[CONF_APP_VERSION] == "2.5.0"
    assert parsed[CONF_DEVICE_MODEL] == "iPhone14,3"
    assert parsed[CONF_PLATFORM_VERSION] == "27.2"
    assert parsed[CONF_DEVICE_ID] == "DEVICE-ID-1"


def test_parse_curl_rejects_text_without_credentials() -> None:
    """Unusable input raises ValueError instead of silently passing."""
    for text in ("", "   ", "curl https://api.smart.jd.com/x", 'curl -H "tgt: t"'):
        try:
            parse_curl_command(text)
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for {text!r}")


def test_parse_curl_accepts_single_quoted_headers() -> None:
    """POSIX style single quotes work too."""
    parsed = parse_curl_command(
        "curl -H 'tgt: T1' -H 'Cookie: a=b' "
        "'https://api.smart.jd.com/p?plat=Android&device_id=D1'"
    )
    assert parsed[CONF_TGT] == "T1"
    assert parsed[CONF_COOKIE] == "a=b"
    assert parsed[CONF_PLATFORM] == "Android"


def test_curl_values_fill_only_blank_form_fields() -> None:
    """Explicitly typed values win over the pasted capture."""
    merged = _apply_curl(
        {
            CONF_CURL: CURL_SAMPLE,
            CONF_COOKIE: "",
            CONF_TGT: "TYPED-TGT",
            CONF_USER_AGENT: "",
        }
    )

    assert merged[CONF_TGT] == "TYPED-TGT"
    assert merged[CONF_COOKIE].startswith("pt_key=app_openKEY")
    assert merged[CONF_USER_AGENT] == "ios"
    assert CONF_CURL not in merged


def test_apply_curl_without_text_is_a_noop() -> None:
    """An empty textarea keeps the manual fields untouched."""
    merged = _apply_curl({CONF_CURL: "", CONF_COOKIE: "c", CONF_TGT: "t"})
    assert merged == {CONF_COOKIE: "c", CONF_TGT: "t"}


def test_apply_curl_reports_missing_credentials() -> None:
    """Bad pasted text surfaces as ValueError so the form can show an error."""
    try:
        _apply_curl({CONF_CURL: 'curl -H "tgt: only"', CONF_COOKIE: ""})
    except ValueError:
        return
    raise AssertionError("expected ValueError")


def test_require_auth_fields_after_merge() -> None:
    """The merged input must still contain both cookie and tgt."""
    merged = _clean_input(_apply_curl({CONF_CURL: CURL_SAMPLE}))
    _require_auth_fields(merged)

    for incomplete in ({CONF_COOKIE: "c"}, {CONF_TGT: "t"}, {}):
        try:
            _require_auth_fields(dict(incomplete))
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for {incomplete!r}")


async def test_manual_auth_accepts_paste_only(hass) -> None:
    """Pasting only a curl command is enough to advance the flow."""
    from custom_components.jd_smart.api import JdSmartDevice

    flow = JdSmartAcConfigFlow()
    flow.hass = hass

    device = JdSmartDevice(
        feed_id="feed-1", name="Plug", category_id="102010", category_name="Socket"
    )

    with (
        patch.object(flow, "_async_current_entries", return_value=[]),
        patch(
            "custom_components.jd_smart.config_flow._refresh_auth",
            new=AsyncMock(),
        ) as refresh,
        patch(
            "custom_components.jd_smart.config_flow._fetch_devices",
            new=AsyncMock(return_value=[device]),
        ),
        patch.object(
            flow,
            "async_step_select_device",
            new=AsyncMock(return_value={"type": "form"}),
        ) as select,
    ):
        await flow.async_step_manual_auth({CONF_CURL: CURL_SAMPLE})

    refresh.assert_awaited_once()
    sent = refresh.await_args.args[1]
    assert sent[CONF_TGT] == "TGT-VALUE"
    assert sent[CONF_COOKIE].startswith("pt_key=app_openKEY")
    assert sent[CONF_DEVICE_ID] == "DEVICE-ID-1"
    select.assert_awaited_once()


async def test_manual_auth_reports_invalid_curl(hass) -> None:
    """Unparsable pasted text keeps the form open with an error."""
    flow = JdSmartAcConfigFlow()
    flow.hass = hass

    result = await flow.async_step_manual_auth({CONF_CURL: "not a curl command"})

    assert result["type"] == "form"
    assert result["errors"] == {"base": "invalid_curl"}


async def test_manual_auth_requires_fields_without_curl(hass) -> None:
    """Manual entry still needs cookie and tgt when no curl is pasted."""
    flow = JdSmartAcConfigFlow()
    flow.hass = hass

    result = await flow.async_step_manual_auth({CONF_COOKIE: "only-cookie"})

    assert result["type"] == "form"
    assert result["errors"] == {"base": "invalid_curl"}


def test_parse_curl_derives_pin_from_cookie() -> None:
    """The account pin comes from pt_pin so token refresh can succeed."""
    parsed = parse_curl_command(
        'curl -H "tgt: T" -H "Cookie: pt_key=k; pt_pin=alice; sid=s" '
        '"https://api.smart.jd.com/x?plat=iPhone"'
    )
    assert parsed[CONF_PIN] == "alice"


def test_parse_curl_decodes_percent_encoded_pin() -> None:
    """A percent-encoded pt_pin is decoded before it is sent to WJLogin."""
    parsed = parse_curl_command(
        'curl -H "tgt: T" -H "Cookie: pt_pin=%E4%B8%AD%E6%96%87" '
        '"https://api.smart.jd.com/x"'
    )
    assert parsed[CONF_PIN] == "\u4e2d\u6587"


def test_parse_curl_without_pt_pin_leaves_pin_unset() -> None:
    """No pt_pin means no derived pin rather than a bogus value."""
    parsed = parse_curl_command(
        'curl -H "tgt: T" -H "Cookie: pt_key=k" "https://api.smart.jd.com/x"'
    )
    assert CONF_PIN not in parsed


def test_form_pin_wins_over_derived_pin() -> None:
    """An explicitly typed pin is never overwritten by the cookie value."""
    merged = _apply_curl(
        {
            CONF_CURL: 'curl -H "tgt: T" -H "Cookie: pt_pin=derived" '
            '"https://api.smart.jd.com/x"',
            CONF_PIN: "typed",
        }
    )
    assert merged[CONF_PIN] == "typed"
