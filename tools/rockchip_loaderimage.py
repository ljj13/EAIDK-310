"""Read Rockchip second-loader image slots used by EAIDK310."""

from dataclasses import dataclass
import hashlib
import struct


HEADER_SIZE = 2048
DEFAULT_SLOT_SIZE = 1024 * 1024
PREFIX_SIZE = 16 * 1024 * 1024
UBOOT_OFFSET = 8 * 1024 * 1024
UBOOT_REGION_SIZE = 4 * 1024 * 1024
_FIXED_HEADER = struct.Struct("<8s6I32sI")


def _build_crc32_rk_table() -> list[int]:
    table: list[int] = []
    for index in range(256):
        crc = index << 24
        for _ in range(8):
            if crc & 0x80000000:
                crc = ((crc << 1) ^ 0x04C11DB7) & 0xFFFFFFFF
            else:
                crc = (crc << 1) & 0xFFFFFFFF
        table.append(crc)
    return table


_CRC32_RK_TABLE = _build_crc32_rk_table()


@dataclass(frozen=True)
class SlotHeader:
    magic: bytes
    version: int
    load_address: int
    load_size: int
    crc32: int
    hash_length: int
    sha256: bytes
    js_hash: int


@dataclass(frozen=True)
class VerificationResult:
    header: SlotHeader
    computed_crc32: int
    crc32_matches: bool


def parse_slot(slot: bytes) -> SlotHeader:
    if len(slot) < HEADER_SIZE:
        raise ValueError("slot is smaller than the 2048-byte Rockchip header")

    magic, version, _reserved, address, size, crc, hash_length, digest, js = (
        _FIXED_HEADER.unpack_from(slot)
    )
    return SlotHeader(magic, version, address, size, crc, hash_length, digest, js)


def unpack_slot(slot: bytes) -> bytes:
    header = parse_slot(slot)
    if header.magic != b"LOADER  ":
        raise ValueError("slot does not contain a Rockchip LOADER header")

    payload_end = HEADER_SIZE + header.load_size
    if payload_end > len(slot):
        raise ValueError("declared payload extends beyond the slot")

    return slot[HEADER_SIZE:payload_end]


def _crc32_rk(data: bytes, crc: int = 0) -> int:
    """Rockchip's non-reflected CRC-32 (polynomial 0x04C11DB7)."""
    for value in data:
        crc = (
            ((crc << 8) & 0xFFFFFFFF)
            ^ _CRC32_RK_TABLE[((crc >> 24) ^ value) & 0xFF]
        )
    return crc & 0xFFFFFFFF


def _js_hash(data: bytes) -> int:
    value = 0x47C6A7E6
    for byte in data:
        value ^= ((value << 5) + byte + (value >> 2)) & 0xFFFFFFFF
        value &= 0xFFFFFFFF
    return value


def _sha256_for_slot(slot: bytes, header: SlotHeader, payload: bytes) -> bytes:
    hashed = bytearray(payload)
    if header.version > 0:
        # The reference tool hashes both version and reserved0 as raw uint32s.
        hashed.extend(slot[8:16])
    hashed.extend(
        struct.pack(
            "<III", header.load_address, header.load_size, header.hash_length
        )
    )
    return hashlib.sha256(hashed).digest()


def _sha256_for_fields(
    payload: bytes,
    *,
    version: int,
    reserved0: int,
    load_address: int,
    hash_length: int,
) -> bytes:
    hashed = bytearray(payload)
    if version > 0:
        hashed.extend(struct.pack("<II", version, reserved0))
    hashed.extend(struct.pack("<III", load_address, len(payload), hash_length))
    return hashlib.sha256(hashed).digest()


def build_slot(
    payload: bytes,
    *,
    load_address: int,
    version: int = 0,
    slot_size: int = DEFAULT_SLOT_SIZE,
    crc32_override: int | None = None,
) -> bytes:
    aligned_payload = payload + (b"\0" * (-len(payload) % 4))
    if HEADER_SIZE + len(aligned_payload) > slot_size:
        raise ValueError("payload does not fit in the Rockchip loader slot")

    reserved0 = 0
    hash_length = 32
    crc32 = (
        _crc32_rk(aligned_payload)
        if crc32_override is None
        else crc32_override & 0xFFFFFFFF
    )
    digest = _sha256_for_fields(
        aligned_payload,
        version=version,
        reserved0=reserved0,
        load_address=load_address,
        hash_length=hash_length,
    )
    js_hash = _js_hash(aligned_payload)

    slot = bytearray(slot_size)
    _FIXED_HEADER.pack_into(
        slot,
        0,
        b"LOADER  ",
        version,
        reserved0,
        load_address,
        len(aligned_payload),
        crc32,
        hash_length,
        digest,
        js_hash,
    )
    slot[HEADER_SIZE : HEADER_SIZE + len(aligned_payload)] = aligned_payload
    return bytes(slot)


def build_redundant_uboot_image(
    payload: bytes,
    *,
    load_address: int = 0x00200000,
    version: int = 0,
    crc32_override: int | None = None,
) -> bytes:
    slot = build_slot(
        payload,
        load_address=load_address,
        version=version,
        crc32_override=crc32_override,
    )
    return slot * 4


def verify_slot(slot: bytes) -> VerificationResult:
    header = parse_slot(slot)
    payload = unpack_slot(slot)

    if header.hash_length != 32:
        raise ValueError(f"unsupported hash length: {header.hash_length}")
    if _js_hash(payload) != header.js_hash:
        raise ValueError("payload JS hash does not match the Rockchip header")
    if _sha256_for_slot(slot, header, payload) != header.sha256:
        raise ValueError("payload SHA-256 does not match the Rockchip header")

    computed_crc32 = _crc32_rk(payload)
    return VerificationResult(
        header=header,
        computed_crc32=computed_crc32,
        crc32_matches=(computed_crc32 == header.crc32),
    )


def verify_redundant_uboot_image(
    region: bytes, *, require_public_crc: bool = False
) -> list[VerificationResult]:
    """Verify the four identical 1 MiB Rockchip U-Boot proper slots."""
    if len(region) != UBOOT_REGION_SIZE:
        raise ValueError("U-Boot region must be exactly 4 MiB")

    slots = [
        region[offset : offset + DEFAULT_SLOT_SIZE]
        for offset in range(0, UBOOT_REGION_SIZE, DEFAULT_SLOT_SIZE)
    ]
    for index, slot in enumerate(slots[1:], start=1):
        if slot != slots[0]:
            raise ValueError(f"slot {index} differs from slot 0")

    results = [verify_slot(slot) for slot in slots]
    if require_public_crc and not all(result.crc32_matches for result in results):
        raise ValueError("Rockchip public CRC check failed")
    return results


def compose_prefix(
    baseline: bytes,
    region: bytes,
    expected_baseline_sha256: str,
    *,
    require_public_crc: bool = False,
) -> bytes:
    """Replace only the 8-12 MiB U-Boot region in a locked 16 MiB prefix."""
    if len(baseline) != PREFIX_SIZE:
        raise ValueError("baseline prefix must be exactly 16 MiB")

    actual_sha256 = hashlib.sha256(baseline).hexdigest()
    if actual_sha256.lower() != expected_baseline_sha256.lower():
        raise ValueError(
            "baseline SHA-256 mismatch: "
            f"expected {expected_baseline_sha256.lower()}, got {actual_sha256}"
        )

    verify_redundant_uboot_image(
        region, require_public_crc=require_public_crc
    )
    composed = bytearray(baseline)
    composed[UBOOT_OFFSET : UBOOT_OFFSET + UBOOT_REGION_SIZE] = region
    return bytes(composed)


def changed_ranges(before: bytes, after: bytes) -> list[tuple[int, int]]:
    """Return coalesced half-open byte ranges that differ."""
    if len(before) != len(after):
        raise ValueError("inputs must have identical lengths")

    ranges: list[tuple[int, int]] = []
    start: int | None = None
    for index, (before_byte, after_byte) in enumerate(zip(before, after)):
        if before_byte != after_byte and start is None:
            start = index
        elif before_byte == after_byte and start is not None:
            ranges.append((start, index))
            start = None
    if start is not None:
        ranges.append((start, len(before)))
    return ranges
