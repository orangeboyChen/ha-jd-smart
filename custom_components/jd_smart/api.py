"""API client for JD Smart."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime
from http import HTTPStatus
import hashlib
import hmac
import json
import secrets
import time
from typing import Any
from urllib.parse import quote, urlencode

from aiohttp import ClientError, ClientResponseError, ClientSession
from cryptography.hazmat.primitives import hashes, padding as crypto_padding
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

try:
    from cryptography.hazmat.decrepit.ciphers.algorithms import (
        TripleDES as TripleDESAlgorithm,
    )
except ImportError:
    TripleDESAlgorithm = algorithms.TripleDES

from .const import (
    APP_KEY,
    CONTROL_PATH,
    DEFAULT_APP_VERSION,
    DEFAULT_CHANNEL,
    DEFAULT_DEVICE_MODEL,
    DEFAULT_PLATFORM,
    DEFAULT_PLATFORM_VERSION,
    DEFAULT_USER_AGENT,
    DEVICE_LIST_PATH,
    HMAC_KEY,
    JD_SMART_BASE_URL,
    LOGGER,
    SNAPSHOT_PATH,
)

WJLOGIN_REFRESH_URL = "https://wlogin.m.jd.com/applogin_v2"
WJLOGIN_APP_ID = 1421
WJLOGIN_APP_NAME = "jdsmart"
WJLOGIN_SDK_VERSION = "12.0.10"
WJLOGIN_RANDOM_KEY_ALPHABET = (
    "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
)
WANGYIN_HANDSHAKE_URL = "http://aks.jdpay.com/handshake"
WANGYIN_SEED_WRAP_KEY = bytes.fromhex(
    "1234567890ABCDEF1234567890ABCDEF"
    "1234567890ABCDEF1234567890ABCDEF"
)


class JdSmartError(Exception):
    """Base JD Smart error."""


class JdSmartAuthError(JdSmartError):
    """Raised on authentication errors."""


class JdSmartCannotConnectError(JdSmartError):
    """Raised when the cloud cannot be reached."""


class JdSmartDecryptError(JdSmartCannotConnectError):
    """Raised when Wangyin encrypted payload cannot be decrypted by server."""


class JdSmartControlError(JdSmartError):
    """Raised when control fails."""


class JdSmartTokenRefreshError(JdSmartAuthError):
    """Raised when JD login token refresh fails."""


@dataclass(slots=True)
class JdSmartCredentials:
    """JD Smart credentials."""

    cookie: str
    tgt: str
    pin: str | None = None
    sgm_context: str | None = None


@dataclass(slots=True)
class JdSmartDeviceProfile:
    """JD Smart device profile."""

    device_id: str
    app_version: str = DEFAULT_APP_VERSION
    platform: str = DEFAULT_PLATFORM
    device_model: str = DEFAULT_DEVICE_MODEL
    platform_version: str = DEFAULT_PLATFORM_VERSION
    channel: str = DEFAULT_CHANNEL
    user_agent: str = DEFAULT_USER_AGENT


@dataclass(slots=True)
class JdSmartSnapshot:
    """JD Smart device snapshot."""

    digest: str
    status: str
    from_device_success: bool
    streams: dict[str, str]

    @classmethod
    def from_result(cls, result: str | dict[str, Any]) -> JdSmartSnapshot:
        """Create snapshot from API result."""
        data = json.loads(result) if isinstance(result, str) else result
        streams = {
            item["stream_id"]: str(item.get("current_value", ""))
            for item in data.get("streams", [])
        }
        return cls(
            digest=str(data.get("digest", "")),
            status=str(data.get("status", "")),
            from_device_success=bool(data.get("fromDeviceSuccess", False)),
            streams=streams,
        )


@dataclass(slots=True)
class JdSmartDevice:
    """JD Smart device entry."""

    feed_id: str
    name: str
    device_id: str | None = None
    category_name: str | None = None
    room_name: str | None = None
    version: str | None = None


@dataclass(slots=True)
class _WangyinSession:
    """Wangyin handshake session."""

    context: bytes
    data_key: bytes


def _json_dumps(data: Any) -> str:
    """Dump compact JSON."""
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def _day_of_year(now: datetime) -> int:
    """Return day of year."""
    return int(now.strftime("%j"))


def _timestamp(now: datetime) -> str:
    """Return API timestamp."""
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def build_authorization(
    method: str,
    raw_body: str,
    profile: JdSmartDeviceProfile,
    now: datetime | None = None,
) -> str:
    """Build JD Smart Authorization header."""
    now = now or datetime.now()
    timestamp = _timestamp(now)
    device_md5 = hashlib.md5(
        (
            f"{profile.platform}{profile.app_version}{profile.device_model}"
            f"{profile.platform_version}:{_day_of_year(now)}"
        ).encode()
    ).hexdigest()
    source = (
        device_md5
        + method.lower()
        + "json_body"
        + raw_body
        + timestamp
        + APP_KEY
        + device_md5
    )
    signature = hmac.new(HMAC_KEY.encode(), source.encode(), hashlib.sha1).digest()
    return f"smart {APP_KEY}:::{base64.b64encode(signature).decode()}:::{timestamp}"


def _pkcs7_pad(data: bytes) -> bytes:
    """Pad bytes for AES block encryption."""
    padder = crypto_padding.PKCS7(128).padder()
    return padder.update(data) + padder.finalize()


def _aes_256_ecb_encrypt(data: bytes, key: bytes, *, pad: bool) -> bytes:
    """Encrypt bytes with AES-256-ECB."""
    plain = _pkcs7_pad(data) if pad else data
    encryptor = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    return encryptor.update(plain) + encryptor.finalize()


def _aes_256_ecb_decrypt(data: bytes, key: bytes) -> bytes:
    """Decrypt bytes with AES-256-ECB without unpadding."""
    decryptor = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
    return decryptor.update(data) + decryptor.finalize()


def _triple_des_ecb_decrypt(data: bytes, key: bytes) -> bytes:
    """Decrypt bytes with 3DES-ECB without unpadding."""
    decryptor = Cipher(TripleDESAlgorithm(key), modes.ECB()).decryptor()
    return decryptor.update(data) + decryptor.finalize()


def _wangyin_token(key: bytes, plain_length: int) -> str:
    """Build the 8-digit Wangyin packet token."""
    key_first_24 = key[:24]
    derived_24 = _triple_des_ecb_decrypt(key_first_24, key_first_24)
    digest = hmac.new(
        derived_24,
        plain_length.to_bytes(8, "big"),
        hashlib.sha256,
    ).digest()
    offset = digest[31] & 0x0F
    value = (
        ((digest[offset] & 0x7F) << 24)
        | (digest[offset + 1] << 16)
        | (digest[offset + 2] << 8)
        | digest[offset + 3]
    )
    return str(value % 100000000).zfill(8)


def _encrypted_body_json(encrypted_body: str) -> str:
    """Build the encrypted body wrapper used by the app."""
    return '{\n  "body" : "' + encrypted_body.replace("/", "\\/") + '"\n}'


def _encode_wangyin_session(plain_text: str, session: _WangyinSession) -> str:
    """Encode plaintext with an established Wangyin handshake session."""
    plain = plain_text.encode()
    encrypted_plain = _aes_256_ecb_encrypt(plain, session.data_key, pad=True)
    header = bytearray(b"0" * 0x84)
    header[0:4] = (1).to_bytes(4, "little")
    header[4:8] = (0x3EB).to_bytes(4, "little")
    header[8:12] = len(encrypted_plain).to_bytes(4, "little")
    header[0x0C:0x14] = _wangyin_token(session.data_key, len(plain)).encode()
    header[0x14:0x64] = session.context
    digest = hmac.new(
        session.data_key[:24],
        bytes(header) + encrypted_plain,
        hashlib.sha256,
    ).digest()
    header[0x64:0x84] = digest
    return base64.b64encode(bytes(header) + encrypted_plain).decode()


def _u32(value: int) -> int:
    """Return value as an unsigned 32-bit integer."""
    return value & 0xFFFFFFFF


def _tea_encrypt_block(block: bytes, key: bytes) -> bytes:
    """Encrypt one TEA block using the JD WJLogin variant."""
    y = int.from_bytes(block[0:4], "big")
    z = int.from_bytes(block[4:8], "big")
    a = int.from_bytes(key[0:4], "big")
    b = int.from_bytes(key[4:8], "big")
    c = int.from_bytes(key[8:12], "big")
    d = int.from_bytes(key[12:16], "big")
    total = 0
    for _ in range(16):
        total = _u32(total + 0x9E3779B9)
        y = _u32(y + _u32(((z << 4) + a) ^ (z + total) ^ ((z >> 5) + b)))
        z = _u32(z + _u32(((y << 4) + c) ^ (y + total) ^ ((y >> 5) + d)))
    return y.to_bytes(4, "big") + z.to_bytes(4, "big")


def _tea_decrypt_block(block: bytes, key: bytes) -> bytes:
    """Decrypt one TEA block using the JD WJLogin variant."""
    y = int.from_bytes(block[0:4], "big")
    z = int.from_bytes(block[4:8], "big")
    a = int.from_bytes(key[0:4], "big")
    b = int.from_bytes(key[4:8], "big")
    c = int.from_bytes(key[8:12], "big")
    d = int.from_bytes(key[12:16], "big")
    total = 0xE3779B90
    for _ in range(16):
        z = _u32(z - _u32(((y << 4) + c) ^ (y + total) ^ ((y >> 5) + d)))
        y = _u32(y - _u32(((z << 4) + a) ^ (z + total) ^ ((z >> 5) + b)))
        total = _u32(total - 0x9E3779B9)
    return y.to_bytes(4, "big") + z.to_bytes(4, "big")


def _key16(key: str) -> bytes:
    """Return a 16-byte WJLogin key."""
    return key.encode()[:16].ljust(16, b"\x00")


def _qqtea_encrypt(data: bytes, key_string: str) -> bytes:
    """Encrypt bytes with the QQTEA mode used by WJLogin."""
    key = _key16(key_string)
    pad_len = (len(data) + 10) % 8
    if pad_len:
        pad_len = 8 - pad_len
    random_bytes = secrets.token_bytes(pad_len + 3)
    plain = bytearray(len(data) + pad_len + 10)
    plain[0] = (random_bytes[0] & 0xF8) | pad_len
    plain[1 : 1 + pad_len + 2] = random_bytes[1:]
    plain[1 + pad_len + 2 : 1 + pad_len + 2 + len(data)] = data

    out = bytearray(len(plain))
    previous_cipher = bytes(8)
    previous_plain = bytes(8)
    for offset in range(0, len(plain), 8):
        mixed = bytes(plain[offset + i] ^ previous_cipher[i] for i in range(8))
        encrypted = _tea_encrypt_block(mixed, key)
        block = bytes(encrypted[i] ^ previous_plain[i] for i in range(8))
        out[offset : offset + 8] = block
        previous_plain = mixed
        previous_cipher = block
    return bytes(out)


def _qqtea_decrypt(cipher: bytes, key_string: str) -> bytes | None:
    """Decrypt bytes with the QQTEA mode used by WJLogin."""
    if len(cipher) < 16 or len(cipher) % 8:
        return None
    key = _key16(key_string)
    plain = bytearray(len(cipher))
    previous_cipher = bytes(8)
    previous_plain = bytes(8)
    for offset in range(0, len(cipher), 8):
        block = bytes(cipher[offset + i] ^ previous_plain[i] for i in range(8))
        mixed = _tea_decrypt_block(block, key)
        plain[offset : offset + 8] = bytes(
            mixed[i] ^ previous_cipher[i] for i in range(8)
        )
        previous_plain = mixed
        previous_cipher = cipher[offset : offset + 8]

    pad_len = plain[0] & 0x07
    start = 1 + pad_len + 2
    end = len(plain) - 7
    if start > end or any(plain[end:]):
        return None
    return bytes(plain[start:end])


def _random_key16() -> str:
    """Generate a WJLogin random key."""
    return "".join(
        WJLOGIN_RANDOM_KEY_ALPHABET[byte % len(WJLOGIN_RANDOM_KEY_ALPHABET)]
        for byte in secrets.token_bytes(16)
    )


def _wj_encrypt_msg(tlv: bytes) -> tuple[str, str]:
    """Return WJLogin random key and encrypted request body."""
    key = _random_key16()
    encrypted = _qqtea_encrypt(tlv, key)
    return key, base64.b64encode(key.encode() + encrypted).decode()


def _wj_decrypt_msg(body: str, key: str) -> bytes | None:
    """Decrypt a WJLogin response body."""
    try:
        cipher = base64.b64decode(body)
    except ValueError:
        return None
    return _qqtea_decrypt(cipher, key)


class _PacketBuilder:
    """Build a WJLogin packet."""

    def __init__(self) -> None:
        """Initialize the builder."""
        self._chunks: list[bytes] = [bytes(2)]

    def short(self, value: int) -> None:
        """Append an unsigned short."""
        self._chunks.append((value & 0xFFFF).to_bytes(2, "big"))

    def byte(self, value: int) -> None:
        """Append one byte."""
        self._chunks.append(bytes([value & 0xFF]))

    def int(self, value: int) -> None:
        """Append a signed int."""
        self._chunks.append(int(value).to_bytes(4, "big", signed=True))

    def long(self, value: int) -> None:
        """Append a signed long."""
        self._chunks.append(int(value).to_bytes(8, "big", signed=True))

    def short_string(self, value: str | None) -> None:
        """Append a short-prefixed string."""
        raw = (value or "").encode()
        self.short(len(raw))
        self._chunks.append(raw)

    def short_bytes(self, value: bytes) -> None:
        """Append short-prefixed bytes."""
        self.short(len(value))
        self._chunks.append(value)

    def tlv(self, tag: int, value: bytes) -> None:
        """Append a TLV field."""
        self.short(tag)
        self.short(len(value))
        self._chunks.append(value)

    def finish(self) -> bytes:
        """Finish packet and write total length."""
        out = bytearray(b"".join(self._chunks))
        out[0:2] = len(out).to_bytes(2, "big")
        return bytes(out)


def _short_string(value: str | None) -> bytes:
    """Return short-prefixed bytes."""
    raw = (value or "").encode()
    return len(raw).to_bytes(2, "big") + raw


def _tlv_app_info(profile: JdSmartDeviceProfile) -> bytes:
    """Build WJLogin app info TLV."""
    builder = _PacketBuilder()
    builder.short(3)
    builder.short(WJLOGIN_APP_ID)
    builder.short_string(profile.platform)
    builder.short_string(profile.platform_version)
    builder.short_string(profile.app_version)
    builder.short_string("")
    builder.short_string(WJLOGIN_APP_NAME)
    builder.short_string("")
    builder.short_string("")
    builder.short_string(profile.device_id)
    builder.int(1)
    builder.short_string(WJLOGIN_SDK_VERSION)
    builder.short_string("")
    builder.short_string("")
    return builder.finish()[2:]


def _tlv_common_union(profile: JdSmartDeviceProfile) -> bytes:
    """Build WJLogin common union TLV."""
    return b"".join(
        [
            _short_string(profile.device_id),
            _short_string(""),
            _short_string(""),
            _short_string("{}"),
        ]
    )


def _tlv_device_101(profile: JdSmartDeviceProfile) -> bytes:
    """Build WJLogin device TLV 101."""
    return b"".join(
        [
            _short_string(""),
            _short_string(""),
            _short_string(""),
            _short_string(profile.device_id),
        ]
    )


def _a2_to_tlv_bytes(tgt: str) -> bytes:
    """Decode URL-safe A2 when possible, otherwise use raw UTF-8."""
    padded = tgt + "=" * ((4 - len(tgt) % 4) % 4)
    try:
        decoded = base64.urlsafe_b64decode(padded)
    except ValueError:
        return tgt.encode()
    round_trip = base64.urlsafe_b64encode(decoded).decode().rstrip("=")
    if decoded and round_trip == tgt.rstrip("="):
        return decoded
    return tgt.encode()


def _base64_url_no_padding(value: bytes) -> str:
    """Encode bytes as URL-safe base64 without padding."""
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _build_refresh_a2_tlv(
    credentials: JdSmartCredentials,
    profile: JdSmartDeviceProfile,
) -> bytes:
    """Build a refreshA2 request packet."""
    builder = _PacketBuilder()
    builder.long(1)
    builder.int(1)
    builder.int(int(time.time()))
    builder.int(0)
    builder.short(3)
    builder.short(2)
    builder.short(WJLOGIN_APP_ID)
    builder.short(273)
    builder.byte(0)
    builder.tlv(8, _tlv_app_info(profile))
    builder.short(10)
    builder.short_bytes(_a2_to_tlv_bytes(credentials.tgt))
    builder.short(16)
    builder.short_string(credentials.pin or "")
    builder.tlv(72, _tlv_common_union(profile))
    builder.tlv(101, _tlv_device_101(profile))
    return builder.finish()


def _parse_refresh_a2_response(packet: bytes) -> str:
    """Parse refreshA2 response packet and return the refreshed TGT."""
    if len(packet) < 31:
        raise JdSmartTokenRefreshError("WJLogin response packet too short")
    reply_code = packet[30]
    if reply_code != 0:
        raise JdSmartTokenRefreshError(f"WJLogin reply code: {reply_code}")

    pos = 31
    while pos + 4 <= len(packet):
        tag = int.from_bytes(packet[pos : pos + 2], "big")
        length = int.from_bytes(packet[pos + 2 : pos + 4], "big")
        pos += 4
        if pos + length > len(packet):
            break
        value = packet[pos : pos + length]
        if tag == 10 and len(value) >= 2:
            return _base64_url_no_padding(value)
        pos += length
    raise JdSmartTokenRefreshError("WJLogin response did not include a new TGT")


def _parse_cookie(cookie: str) -> list[tuple[str, str]]:
    """Parse a Cookie header into key-value pairs."""
    items: list[tuple[str, str]] = []
    for part in cookie.split(";"):
        part = part.strip()
        if not part:
            continue
        key, separator, value = part.partition("=")
        items.append((key.strip(), value.strip() if separator else ""))
    return items


def _upsert_cookie(items: list[tuple[str, str]], key: str, value: str) -> None:
    """Insert or update a Cookie item."""
    for index, (item_key, _item_value) in enumerate(items):
        if item_key.lower() == key.lower():
            items[index] = (key, value)
            return
    items.append((key, value))


def _build_cookie_from_tgt(cookie: str, tgt: str, pin: str | None) -> str:
    """Update a Cookie header with the refreshed WJLogin token."""
    items = _parse_cookie(cookie)
    if pin:
        encoded_pin = quote(pin, safe="")
        _upsert_cookie(items, "pin", encoded_pin)
        _upsert_cookie(items, "pt_pin", encoded_pin)
        _upsert_cookie(items, "pwdt_id", encoded_pin)
    _upsert_cookie(items, "wskey", tgt)
    return "; ".join(f"{key}={value}" for key, value in items)


class JdSmartClient:
    """JD Smart client."""

    def __init__(
        self,
        session: ClientSession,
        credentials: JdSmartCredentials,
        profile: JdSmartDeviceProfile,
    ) -> None:
        """Initialize the client."""
        self._session = session
        self.credentials = credentials
        self.profile = profile
        self._wangyin_session: _WangyinSession | None = None

    def _public_query(self) -> dict[str, str]:
        """Build public query parameters."""
        return {
            "plat": self.profile.platform,
            "app_version": self.profile.app_version,
            "hard_platform": self.profile.device_model,
            "plat_version": self.profile.platform_version,
            "device_id": self.profile.device_id,
            "channel": self.profile.channel,
        }

    def _headers(
        self, raw_body: str, *, content_type: str = "application/json; charset=utf-8"
    ) -> dict[str, str]:
        """Build common headers."""
        authorization = build_authorization("POST", raw_body, self.profile)
        headers = {
            "Content-Type": content_type,
            "Authorization": authorization,
            "Cookie": self.credentials.cookie,
            "Accept": "*/*",
            "tgt": self.credentials.tgt,
            "app_identity": "WL",
            "appversion": self.profile.app_version,
            "appplatform": self.profile.device_model,
            "appplatformversion": self.profile.platform_version,
            "User-Agent": self.profile.user_agent,
            "ef": "1",
        }
        if self.credentials.sgm_context:
            headers["Sgm-Context"] = self.credentials.sgm_context
        return headers

    async def _async_start_wangyin_handshake(self) -> _WangyinSession:
        """Start a Wangyin handshake and return a session."""
        private_key = ec.generate_private_key(ec.SECP256K1())
        private_scalar = private_key.private_numbers().private_value.to_bytes(32, "big")
        public_key = private_key.public_key().public_bytes(
            encoding=Encoding.X962,
            format=PublicFormat.CompressedPoint,
        )
        encrypted_private_key = _aes_256_ecb_encrypt(
            private_scalar, WANGYIN_SEED_WRAP_KEY, pad=False
        )
        record = bytearray(b"0" * 0x106)
        record[0:4] = (1).to_bytes(4, "little")
        record[4:8] = (0x3E9).to_bytes(4, "little")
        record[0x84:0xC4] = encrypted_private_key.hex().upper().encode()
        record[0xC4:0x106] = public_key.hex().upper().encode()

        try:
            async with self._session.post(
                WANGYIN_HANDSHAKE_URL,
                data=base64.b64encode(bytes(record)).decode(),
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Host": "aks.jdpay.com:80",
                    "wpe": "jdjr",
                },
            ) as response:
                text = await response.text()
                if response.status != HTTPStatus.OK:
                    raise JdSmartCannotConnectError(
                        f"Wangyin handshake HTTP status: {response.status}"
                    )
        except (ClientError, TimeoutError) as err:
            raise JdSmartCannotConnectError("Unable to reach Wangyin handshake") from err

        try:
            decoded = base64.b64decode(text)
        except ValueError as err:
            raise JdSmartCannotConnectError("Invalid Wangyin handshake response") from err
        if len(decoded) < 0x106:
            raise JdSmartCannotConnectError("Wangyin handshake response too short")
        code = int.from_bytes(decoded[4:8], "little")
        if code != 0x3EA:
            raise JdSmartCannotConnectError(f"Wangyin handshake failed: {code}")

        context = bytearray(decoded[0x14:0x64])
        encrypted_scalar = bytes.fromhex(decoded[0x84:0xC4].decode())
        server_scalar = _aes_256_ecb_decrypt(encrypted_scalar, WANGYIN_SEED_WRAP_KEY)
        server_public_key = ec.EllipticCurvePublicKey.from_encoded_point(
            ec.SECP256K1(), bytes.fromhex(decoded[0xC4:0x106].decode())
        )
        server_private_key = ec.derive_private_key(
            int.from_bytes(server_scalar, "big"), ec.SECP256K1()
        )
        shared_secret = server_private_key.exchange(ec.ECDH(), server_public_key)
        digest = hashes.Hash(hashes.SHA256())
        digest.update(shared_secret)
        data_key = digest.finalize()
        context[0x30:0x50] = _aes_256_ecb_encrypt(
            data_key, WANGYIN_SEED_WRAP_KEY, pad=False
        )
        LOGGER.debug("JD Smart Wangyin handshake succeeded")
        return _WangyinSession(bytes(context), data_key)

    async def _async_wangyin_encode(self, plain_text: str) -> str:
        """Encode text with a cached Wangyin session."""
        if self._wangyin_session is None:
            self._wangyin_session = await self._async_start_wangyin_handshake()
        return _encode_wangyin_session(plain_text, self._wangyin_session)

    async def _request_wangyin_json(
        self,
        path: str,
        raw_body: str,
    ) -> dict[str, Any]:
        """POST a Wangyin-encrypted request."""
        for attempt in range(2):
            raw_query = _json_dumps(self._public_query())
            ep = await self._async_wangyin_encode(raw_query)
            encrypted_body = await self._async_wangyin_encode(raw_body)
            wrapped_body = _encrypted_body_json(encrypted_body)
            url = f"{JD_SMART_BASE_URL}{path}?ep={quote(ep, safe='')}"
            try:
                return await self._request_json(
                    url,
                    wrapped_body,
                    headers=self._headers(raw_body),
                )
            except JdSmartDecryptError:
                self._wangyin_session = None
                if attempt == 0:
                    LOGGER.warning(
                        "JD Smart Wangyin decrypt failed; retrying once: path=%s",
                        path,
                    )
                    continue
                LOGGER.warning(
                    "JD Smart Wangyin decrypt failed after retry: path=%s",
                    path,
                )
                raise
            except JdSmartError as err:
                self._wangyin_session = None
                raise err
        raise JdSmartDecryptError("Wangyin decrypt failed")

    async def _request_json(
        self,
        url: str,
        raw_body: str,
        *,
        headers: dict[str, str],
    ) -> dict[str, Any]:
        """POST JSON and parse response."""
        LOGGER.debug(
            "JD Smart request: path=%s, body_length=%s",
            url.split("?", 1)[0],
            len(raw_body),
        )
        try:
            async with self._session.post(
                url, data=raw_body, headers=headers
            ) as response:
                text = await response.text()
                LOGGER.debug(
                    "JD Smart response: path=%s, http_status=%s, body_length=%s",
                    url.split("?", 1)[0],
                    response.status,
                    len(text),
                )
                if response.status != HTTPStatus.OK:
                    LOGGER.warning(
                        "JD Smart HTTP error: path=%s, http_status=%s, body=%s",
                        url.split("?", 1)[0],
                        response.status,
                        _truncate(text),
                    )
                    raise ClientResponseError(
                        response.request_info,
                        response.history,
                        status=response.status,
                        message=text,
                        headers=response.headers,
                    )
        except (ClientError, TimeoutError) as err:
            raise JdSmartCannotConnectError from err

        try:
            payload = json.loads(text)
        except json.JSONDecodeError as err:
            raise JdSmartCannotConnectError("Invalid JSON response") from err

        if str(payload.get("code", "")) == "604":
            raise JdSmartDecryptError(payload.get("msg", "Wangyin decrypt failed"))

        error = payload.get("error")
        if error:
            error_code = str(error.get("errorCode", ""))
            error_info = error.get("errorInfo", "JD Smart API error")
            LOGGER.warning(
                "JD Smart API error: path=%s, code=%s, info=%s, status=%s",
                url.split("?", 1)[0],
                error_code,
                error_info,
                payload.get("status"),
            )
            if error_code == "401":
                raise JdSmartAuthError(error_info)
            raise JdSmartError(error_info)
        if payload.get("status") not in (0, "0"):
            LOGGER.warning(
                "JD Smart unexpected status: path=%s, status=%s, payload=%s",
                url.split("?", 1)[0],
                payload.get("status"),
                _truncate(json.dumps(payload, ensure_ascii=False)),
            )
            raise JdSmartError(f"Unexpected status: {payload.get('status')}")
        return payload

    async def async_refresh_token(self) -> tuple[str, str]:
        """Refresh the JD WJLogin A2 token and update local credentials."""
        tlv = _build_refresh_a2_tlv(self.credentials, self.profile)
        random_key, raw_body = _wj_encrypt_msg(tlv)
        LOGGER.info("JD Smart token refresh started")
        try:
            async with self._session.post(
                WJLOGIN_REFRESH_URL,
                data=raw_body,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "User-Agent": f"Android WJLoginSDK {WJLOGIN_SDK_VERSION}",
                },
            ) as response:
                text = await response.text()
                if response.status != HTTPStatus.OK:
                    LOGGER.warning(
                        "JD Smart token refresh HTTP error: http_status=%s, "
                        "body_length=%s",
                        response.status,
                        len(text),
                    )
                    raise JdSmartTokenRefreshError(
                        f"WJLogin HTTP status: {response.status}"
                    )
        except (ClientError, TimeoutError) as err:
            raise JdSmartTokenRefreshError("Unable to reach WJLogin") from err

        packet = _wj_decrypt_msg(text, random_key)
        if packet is None:
            raise JdSmartTokenRefreshError("Unable to decrypt WJLogin response")

        new_tgt = _parse_refresh_a2_response(packet)
        new_cookie = _build_cookie_from_tgt(
            self.credentials.cookie,
            new_tgt,
            self.credentials.pin,
        )
        same_token = new_tgt == self.credentials.tgt
        self.credentials.tgt = new_tgt
        self.credentials.cookie = new_cookie
        LOGGER.info("JD Smart token refresh succeeded: same_token=%s", same_token)
        return new_tgt, new_cookie

    async def async_get_devices(self) -> list[JdSmartDevice]:
        """Fetch selectable JD Smart devices."""
        raw_body = _json_dumps({"json": {"version": "2.0"}})
        payload = await self._request_wangyin_json(DEVICE_LIST_PATH, raw_body)
        result = payload.get("result")
        data = json.loads(result) if isinstance(result, str) else result
        devices = _parse_devices(data)
        if not devices:
            raise JdSmartError("No JD Smart devices found")
        return devices

    async def async_get_snapshot(
        self,
        feed_id: str,
        digest: str = "",
    ) -> JdSmartSnapshot:
        """Fetch a device snapshot using the plain iOS endpoint."""
        inner: dict[str, str | int] = {
            "feed_id": feed_id,
            "digest": digest,
            "pullMode": 0,
            "version": "2.0",
        }
        raw_body = _json_dumps({"json": inner})
        url = (
            f"{JD_SMART_BASE_URL}{SNAPSHOT_PATH}"
            f"?{urlencode(self._public_query())}"
        )
        payload = await self._request_json(
            url,
            raw_body,
            headers=self._headers(raw_body),
        )
        return JdSmartSnapshot.from_result(payload["result"])

    def _control_body(self, feed_id: str, commands: dict[str, Any]) -> str:
        """Build control business body."""
        inner = {
            "version": "2.0",
            "feed_id": feed_id,
            "command": [
                {"stream_id": stream_id, "current_value": str(value)}
                for stream_id, value in commands.items()
            ],
        }
        return _json_dumps({"json": _json_dumps(inner)})

    async def async_control_streams(
        self,
        feed_id: str,
        commands: dict[str, Any],
    ) -> JdSmartSnapshot | None:
        """Control device streams."""
        raw_body = self._control_body(feed_id, commands)
        LOGGER.info(
            "JD Smart control command: feed_id=%s, commands=%s",
            feed_id,
            commands,
        )

        payload = await self._request_wangyin_json(CONTROL_PATH, raw_body)
        result = json.loads(payload["result"])
        LOGGER.info(
            "JD Smart control result: feed_id=%s, control_ret=%s, "
            "status=%s, has_streams=%s, digest=%s",
            feed_id,
            result.get("control_ret"),
            result.get("status"),
            "streams" in result,
            result.get("digest"),
        )
        if "streams" in result:
            return JdSmartSnapshot.from_result(result)
        if result.get("control_ret") == "done":
            return None
        if result.get("status") in (1, "1"):
            return JdSmartSnapshot.from_result(result)
        LOGGER.warning(
            "JD Smart unexpected control result: feed_id=%s, result=%s",
            feed_id,
            _truncate(json.dumps(result, ensure_ascii=False)),
        )
        raise JdSmartControlError(f"Unexpected control result: {result}")


def _truncate(value: str, limit: int = 1000) -> str:
    """Truncate a log value."""
    if len(value) <= limit:
        return value
    return f"{value[:limit]}..."


def _parse_devices(data: Any) -> list[JdSmartDevice]:
    """Parse device entries from a nested device-list response."""
    devices: list[JdSmartDevice] = []
    seen: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                visit(item)
            return
        if not isinstance(value, dict):
            return

        feed_id = value.get("feed_id", value.get("feedId"))
        is_device = (
            feed_id is not None
            or value.get("device_id") is not None
            or value.get("deviceId") is not None
            or value.get("card_name") is not None
            or value.get("cardName") is not None
        )
        if is_device and feed_id is not None:
            feed_id_text = str(feed_id)
            if feed_id_text not in seen:
                seen.add(feed_id_text)
                name = (
                    value.get("card_name")
                    or value.get("cardName")
                    or value.get("name")
                    or value.get("device_name")
                    or value.get("deviceName")
                    or feed_id_text
                )
                devices.append(
                    JdSmartDevice(
                        feed_id=feed_id_text,
                        name=str(name),
                        device_id=_optional_str(
                            value.get("device_id", value.get("deviceId"))
                        ),
                        category_name=_optional_str(
                            value.get("category_name", value.get("categoryName"))
                        ),
                        room_name=_optional_str(
                            value.get("room_name", value.get("roomName"))
                        ),
                        version=_optional_str(value.get("version")),
                    )
                )

        for child in value.values():
            visit(child)

    visit(data)
    return devices


def _optional_str(value: Any) -> str | None:
    """Return value as a string if present."""
    return None if value is None else str(value)
