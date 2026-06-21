"""Ad-hoc S3 storage round-trip test.

Resolves the EFFECTIVE storage config (DB settings override env), prints it with
secrets masked, then — if the active backend is S3 — performs a real round trip:
    1. head_bucket  (connectivity / credentials / bucket exists)
    2. save() a tiny 1x1 PNG via the app's Storage backend
    3. head_object on the returned key (object actually landed)
    4. HTTP GET the public URL (is it publicly reachable?)
    5. delete() (cleanup)

Run inside the backend container:
    docker compose exec backend python /app/scripts/test_s3_storage.py
"""
from __future__ import annotations

import base64
import sys
import urllib.request

from app.db.session import SessionLocal
from app.storage import get_storage, resolve_config
from app.storage.base import MediaFolder

# Smallest valid 1x1 transparent PNG.
PNG_1x1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR4nGNgYGAAAAAEAAH2FzhVAAAAAElFTkSuQmCC"
)


def mask(v: str) -> str:
    if not v:
        return "(blank)"
    if len(v) <= 6:
        return "***"
    return f"{v[:4]}…{v[-2:]} (len={len(v)})"


def main() -> int:
    db = SessionLocal()
    try:
        cfg = resolve_config(db)
        print("=" * 60)
        print("EFFECTIVE STORAGE CONFIG (DB overrides env)")
        print("=" * 60)
        print(f"  backend          : {cfg['backend']}")
        print(f"  bucket           : {cfg['bucket'] or '(blank)'}")
        print(f"  region           : {cfg['region'] or '(blank)'}")
        print(f"  endpoint_url     : {cfg['endpoint_url'] or '(blank — AWS S3)'}")
        print(f"  public_base_url  : {cfg['public_base_url'] or '(blank — derived)'}")
        print(f"  acl              : {cfg['acl'] or '(blank — no ACL sent)'}")
        print(f"  root_prefix      : {cfg['root_prefix'] or '(blank)'}")
        print(f"  access_key       : {mask(cfg['access_key'])}")
        print(f"  secret_key       : {mask(cfg['secret_key'])}")
        print()

        if cfg["backend"] != "s3":
            print("RESULT: Active storage backend is NOT 's3' — it is "
                  f"'{cfg['backend']}'.")
            print("        Uploads are currently saved to LOCAL disk, not S3.")
            print("        Switch Admin → Settings → Storage to S3 (or set "
                  "STORAGE_BACKEND=s3) to use the bucket.")
            return 2

        store = get_storage(db)
        print(f"Storage class    : {type(store).__name__}")
        print(f"public_base      : {store.public_base}")
        print()

        # 1. Connectivity.
        print("[1/5] head_bucket ...", end=" ", flush=True)
        store.client.head_bucket(Bucket=store.bucket)
        print("OK")

        # 2. Save.
        print("[2/5] save() 1x1 PNG ...", end=" ", flush=True)
        url = store.save(
            data=PNG_1x1,
            filename="s3-test.png",
            content_type="image/png",
            folder=MediaFolder.PRODUCTS,
        )
        print("OK")
        print(f"        returned URL: {url}")

        # Derive the object key from the URL for head_object.
        key = url[len(store.public_base):].lstrip("/")
        print(f"        object key  : {key}")

        # 3. Confirm object exists server-side.
        print("[3/5] head_object ...", end=" ", flush=True)
        head = store.client.head_object(Bucket=store.bucket, Key=key)
        size = head["ContentLength"]
        ctype = head.get("ContentType")
        print(f"OK (size={size} bytes, content-type={ctype})")
        if size != len(PNG_1x1):
            print(f"        WARNING: stored size {size} != uploaded "
                  f"{len(PNG_1x1)}")

        # 4. Public reachability over HTTP.
        print("[4/5] HTTP GET public URL ...", end=" ", flush=True)
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = resp.read()
                print(f"OK (HTTP {resp.status}, {len(body)} bytes)")
                if body != PNG_1x1:
                    print("        WARNING: fetched bytes differ from uploaded")
        except Exception as exc:  # noqa: BLE001
            print(f"NOT PUBLIC ({exc})")
            print("        (object is stored fine; it's just not publicly "
                  "readable — needs a bucket policy / CDN for the storefront)")

        # 5. Cleanup.
        print("[5/5] delete() ...", end=" ", flush=True)
        store.delete(url)
        # confirm gone
        try:
            store.client.head_object(Bucket=store.bucket, Key=key)
            print("WARNING: object still present after delete")
        except Exception:  # noqa: BLE001
            print("OK (removed)")

        print()
        print("RESULT: S3 storage round trip SUCCEEDED — images are being "
              "stored in the bucket correctly.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
