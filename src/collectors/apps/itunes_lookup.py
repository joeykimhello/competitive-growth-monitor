"""iTunes Lookup API collector — iOS app version.

No API key required. Public Apple endpoint.
Docs: https://developer.apple.com/library/archive/documentation/AudioVideo/Conceptual/iTuneSearchAPI/

Usage:
    result = collect(app_id="401626263", country="kr")

Returns:
    {
        app_name:     str,
        bundle_id:    str,
        version:      str,        # e.g. "24.18"
        release_date: str,        # ISO 8601 — currentVersionReleaseDate
        source_url:   str,
        status:       "ok" | "not_found" | "failed",
        error:        str | None,
    }
"""

import sys

import requests

_BASE_URL = "https://itunes.apple.com/lookup"
_TIMEOUT = 15


def collect(app_id: str, country: str = "kr") -> dict:
    """Fetch latest iOS version info via iTunes Lookup API."""
    source_url = f"{_BASE_URL}?id={app_id}&country={country}"
    base: dict = {
        "app_name": "",
        "bundle_id": "",
        "version": "",
        "release_date": "",
        "source_url": source_url,
        "status": "failed",
        "error": None,
    }
    try:
        resp = requests.get(source_url, timeout=_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        msg = f"{type(exc).__name__}: {exc}"
        print(f"  [ITUNES] Request failed app_id={app_id}: {msg}", file=sys.stderr)
        base["error"] = msg
        return base
    except ValueError as exc:
        msg = f"JSON parse error: {exc}"
        print(f"  [ITUNES] {msg} app_id={app_id}", file=sys.stderr)
        base["error"] = msg
        return base

    results = data.get("results", [])
    if not results:
        print(f"  [ITUNES] Not found: app_id={app_id} country={country}")
        base["status"] = "not_found"
        return base

    r = results[0]
    version = r.get("version", "")
    release_date = r.get("currentVersionReleaseDate", "") or r.get("releaseDate", "")
    app_name = r.get("trackName", "")
    bundle_id = r.get("bundleId", "")
    release_notes = (r.get("releaseNotes") or "").strip()

    print(
        f"  [ITUNES] app_id={app_id} app_name={app_name!r}"
        f" version={version!r} release_date={release_date!r}"
        f" release_notes_len={len(release_notes)}"
    )
    return {
        "app_name": app_name,
        "bundle_id": bundle_id,
        "version": version,
        "release_date": release_date,
        "release_notes": release_notes,
        "source_url": source_url,
        "status": "ok",
        "error": None,
    }
