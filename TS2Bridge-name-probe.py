"""Read only probe for Sims 2 character names and neighborhood identities.

Usage: py TS2Bridge-name-probe.py path/to/G001_User00019.package
       py TS2Bridge-name-probe.py path/to/G001_Neighborhood.package
       py TS2Bridge-name-probe.py path/to/G001 --output TS2Bridge-G001-names.json
"""
from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path


def u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def qfs_decode(data: bytes) -> bytes:
    if len(data) < 9 or data[4:6] not in (b"\x10\xfb", b"\x50\xfb"):
        return data
    target = int.from_bytes(data[6:9], "big")
    if target > 16_000_000:
        raise ValueError("QFS resource too large")
    out = bytearray()
    pos = 9
    while pos < len(data) and len(out) < target:
        cc = data[pos]
        pos += 1
        if cc >= 0xFC:
            plain, copied, distance = cc & 3, 0, 0
        elif cc >= 0xE0:
            plain, copied, distance = ((cc & 31) + 1) * 4, 0, 0
        elif cc >= 0xC0:
            a, b, c = data[pos : pos + 3]
            pos += 3
            plain = cc & 3
            copied = ((cc & 12) << 6) + c + 5
            distance = ((cc & 16) << 12) + (a << 8) + b + 1
        elif cc >= 0x80:
            a, b = data[pos : pos + 2]
            pos += 2
            plain = a >> 6
            copied = (cc & 63) + 4
            distance = ((a & 63) << 8) + b + 1
        else:
            a = data[pos]
            pos += 1
            plain = cc & 3
            copied = ((cc & 28) >> 2) + 3
            distance = ((cc & 96) << 3) + a + 1
        if pos + plain > len(data) or len(out) + plain + copied > target:
            raise ValueError("truncated or oversized QFS command")
        out.extend(data[pos : pos + plain])
        pos += plain
        if copied and (distance < 1 or distance > len(out)):
            raise ValueError("invalid QFS back reference")
        for _ in range(copied):
            out.append(out[-distance])
    if len(out) != target:
        raise ValueError(f"QFS size mismatch: {len(out)} != {target}")
    return bytes(out)


def entries(data: bytes):
    if len(data) < 96 or data[:4] != b"DBPF":
        raise ValueError("not a DBPF package")
    count, offset, size, minor = u32(data, 36), u32(data, 40), u32(data, 44), u32(data, 60)
    width = 24 if minor == 2 else 20
    if count > 500_000 or size != count * width or offset + size > len(data):
        raise ValueError("invalid DBPF index")
    for i in range(count):
        row = struct.unpack_from("<6I" if width == 24 else "<5I", data, offset + i * width)
        typ, group, inst = row[:3]
        start, length = row[-2:]
        if start + length > len(data):
            raise ValueError("resource outside package")
        if typ in (0x43545353, 0x4F424A44, 0xAACE2EFB):
            yield typ, group, inst, qfs_decode(data[start : start + length])


def catalog_strings(data: bytes) -> dict[int, list[str]]:
    if len(data) < 68 or data[64:66] != b"\xfd\xff":
        raise ValueError("unsupported catalog string layout")
    count = struct.unpack_from("<H", data, 66)[0]
    pos = 68
    langs: dict[int, list[str]] = {}
    for _ in range(count):
        if pos >= len(data):
            raise ValueError("short catalog string list")
        lang = data[pos]
        pos += 1
        end = data.index(b"\0", pos)
        value = data[pos:end].decode("utf-8", errors="replace")
        pos = data.index(b"\0", end + 1) + 1  # Skip text source annotation.
        langs.setdefault(lang, []).append(value)
    return langs


def character(data: bytes) -> dict:
    guid = None
    catalogs = {}
    for typ, group, inst, content in entries(data):
        if typ == 0x4F424A44 and len(content) >= 0x60:
            guid = u32(content, 0x5C)
        elif typ == 0x43545353 and inst in (0, 0x7D0):
            catalogs[inst] = catalog_strings(content)
    langs = catalogs.get(0x7D0) or catalogs.get(0) or {}
    names = langs.get(1) or next(iter(langs.values()), [])
    if not guid or not names or len(names) < 3:
        raise ValueError("missing Sim GUID or first/last name")
    return {"simGuid": f"{guid:08X}", "firstName": names[0], "lastName": names[2],
            "description": names[1].strip() or None}


def neighborhood(data: bytes) -> list[dict]:
    sims = []
    seen_nids = set()
    for typ, group, inst, content in entries(data):
        if typ != 0xAACE2EFB:
            continue
        # The supplied G001 file uses SDSC version 0x71, which stores the
        # neighbor instance and Sim GUID at 0x1DA/0x1DC. Earlier layouts use
        # the legacy positions documented for their resource versions.
        if len(content) >= 0x1E0 and u32(content, 0) == 0x71:
            nid = struct.unpack_from("<H", content, 0x1DA)[0]
            guid = u32(content, 0x1DC)
        elif len(content) >= 0x166:
            nid = struct.unpack_from("<H", content, 0x4A)[0]
            guid = u32(content, 0x162)
        else:
            continue
        if nid and guid:
            if nid in seen_nids:
                raise ValueError(f"duplicate neighbor ID {nid} in neighborhood")
            seen_nids.add(nid)
            sims.append({"nid": nid, "simGuid": f"{guid:08X}",
                         "descriptionInstance": inst})
    return sims


def map_folder(path: Path, overrides_path: Path | None = None) -> dict:
    hoods = list(path.glob("*_Neighborhood.package"))
    if len(hoods) != 1 or not (path / "Characters").is_dir():
        raise ValueError("expected one neighborhood package and a Characters folder")
    by_guid: dict[str, dict] = {}
    for sim in neighborhood(hoods[0].read_bytes()):
        if sim["simGuid"] in by_guid:
            raise ValueError("duplicate Sim GUID in neighborhood")
        by_guid[sim["simGuid"]] = sim
    resolved = []
    for char_file in sorted((path / "Characters").glob("*.package")):
        try:
            identity = character(char_file.read_bytes())
        except ValueError:
            continue
        description = by_guid.get(identity["simGuid"])
        if description:
            resolved.append({"nid": description["nid"], "simGuid": identity["simGuid"],
                             "firstName": identity["firstName"], "lastName": identity["lastName"],
                             "description": identity["description"]})
    if overrides_path is not None:
        overrides = json.loads(overrides_path.read_text(encoding="utf-8"))
        if overrides.get("neighborhood") != path.name:
            raise ValueError("identity override neighborhood does not match folder")
        known_nids = {entry["nid"] for entry in resolved}
        for entry in overrides.get("resolved", []):
            nid, guid = entry.get("nid"), entry.get("simGuid")
            if (entry.get("source") != "operator-confirmed" or
                    type(nid) is not int or nid <= 0 or
                    type(guid) is not str or by_guid.get(guid, {}).get("nid") != nid or
                    not all(isinstance(entry.get(k), str) and entry[k]
                            for k in ("firstName", "lastName"))):
                raise ValueError("invalid identity override or mismatched neighborhood GUID")
            if nid not in known_nids:
                resolved.append({"nid": nid, "simGuid": guid,
                                 "firstName": entry["firstName"],
                                 "lastName": entry["lastName"],
                                 "description": None, "source": "operator-confirmed"})
                known_nids.add(nid)
    return {"neighborhood": path.name, "resolved": resolved,
            "descriptionCount": len(by_guid)}


def main(path: Path, events_path: Path | None = None,
         overrides_path: Path | None = None) -> dict:
    if path.is_dir():
        result = map_folder(path, overrides_path)
        if events_path is not None:
            by_nid = {entry["nid"]: entry for entry in result["resolved"]}
            labeled = []
            for line in events_path.read_text(encoding="utf-8").splitlines():
                event = json.loads(line)
                match = by_nid.get(event.get("nid"))
                if match:
                    event = {**event, "firstName": match["firstName"],
                             "lastName": match["lastName"]}
                labeled.append(event)
            result["events"] = labeled
        return result
    data = path.read_bytes()
    if "_Neighborhood.package" in path.name:
        return {"file": path.name, "sims": neighborhood(data)}
    return {"file": path.name, **character(data)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path, help="character package, neighborhood package, or neighborhood folder")
    parser.add_argument("--events", type=Path, help="optional event JSONL for the same neighborhood")
    parser.add_argument("--output", type=Path, help="write result to this JSON file instead of stdout")
    parser.add_argument("--overrides", type=Path,
                        help="operator-confirmed names guarded by current neighborhood NID and GUID")
    args = parser.parse_args()
    result = json.dumps(main(args.path, args.events, args.overrides), ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(result, encoding="utf-8")
    else:
        print(result, end="")
