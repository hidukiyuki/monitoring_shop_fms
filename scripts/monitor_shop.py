import csv
import hashlib
import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


# ============================================================
# Configuration
# ============================================================

CONFIG_FILE = Path("config/monitors.json")

DATA_DIR = Path("data")
DOCS_DATA_DIR = Path("docs/data")

DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK", "")

# 前回の商品数に対して、この割合を下回った場合は異常とみなす
MIN_COUNT_RATIO = 0.5

# HTTP
REQUEST_TIMEOUT = 30
USER_AGENT = "Mozilla/5.0 GitHubActions ShopifyMonitor/1.0"

# リクエスト間隔
REQUEST_INTERVAL = 2


# ============================================================
# Utility
# ============================================================

def now_iso():
    return datetime.now(timezone.utc).isoformat()


def load_json(path, default=None):
    if not path.exists():
        return default

    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"failed to read JSON: {path}: {e}")
        return default


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)

    temp = path.with_suffix(path.suffix + ".tmp")

    with temp.open("w", encoding="utf-8") as f:
        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2
        )

    temp.replace(path)


def sha256(data):
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def safe_filename(value):
    return "".join(
        c if c.isalnum() or c in "-_" else "_"
        for c in value
    )


# ============================================================
# Discord
# ============================================================

def send_discord(message):
    if not DISCORD_WEBHOOK:
        print("DISCORD_WEBHOOK is not configured.")
        return

    payload = json.dumps({
        "content": message
    }).encode("utf-8")

    request = Request(
        DISCORD_WEBHOOK,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT
        },
        method="POST"
    )

    try:
        with urlopen(request, timeout=15) as response:
            print(f"Discord notification: HTTP {response.status}")
    except Exception as e:
        print(f"Discord notification failed: {e}")


def send_error(message):
    print(message)
    send_discord(f"🚨 **FINDME STORE監視エラー**\n\n{message}")


# ============================================================
# Shopify API
# ============================================================

def fetch_page(url, page):
    separator = "&" if "?" in url else "?"

    request_url = (
        f"{url}"
        f"{separator}limit=250"
        f"&page={page}"
    )

    print(f"GET {request_url}")

    request = Request(
        request_url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json"
        }
    )

    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            status = response.status
            raw = response.read()

        if status != 200:
            raise RuntimeError(
                f"HTTP status {status}"
            )

        if not raw:
            raise RuntimeError(
                "empty HTTP response"
            )

        text = raw.decode("utf-8")

        if not text.strip():
            raise RuntimeError(
                "empty response body"
            )

        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise RuntimeError(
                f"invalid JSON response: {e}"
            )

        if not isinstance(data, dict):
            raise RuntimeError(
                f"unexpected JSON root: {type(data).__name__}"
            )

        products = data.get("products")

        if products is None:
            raise RuntimeError(
                "JSON does not contain 'products'"
            )

        if not isinstance(products, list):
            raise RuntimeError(
                f"'products' is not a list: {type(products).__name__}"
            )

        return products

    except HTTPError as e:
        raise RuntimeError(
            f"HTTPError {e.code}: {e.reason}"
        )

    except URLError as e:
        raise RuntimeError(
            f"URLError: {e.reason}"
        )


# ============================================================
# Normalization
# ============================================================

def normalize_variant(variant):
    if not isinstance(variant, dict):
        return None

    return {
        "id": variant.get("id"),
        "title": variant.get("title"),
        "sku": variant.get("sku"),
        "created_at": variant.get("created_at"),
        "updated_at": variant.get("updated_at"),
        "price": variant.get("price"),
        "compare_at_price": variant.get("compare_at_price"),
        "available": variant.get("available"),
        "inventory_quantity": variant.get("inventory_quantity")
    }


def normalize_image(image):
    if not isinstance(image, dict):
        return None

    return {
        "id": image.get("id"),
        "src": image.get("src"),
        "alt": image.get("alt")
    }


def normalize_product(product):
    if not isinstance(product, dict):
        return None

    variants = []

    for variant in product.get("variants") or []:
        normalized = normalize_variant(variant)

        if normalized is not None:
            variants.append(normalized)

    images = []

    for image in product.get("images") or []:
        normalized = normalize_image(image)

        if normalized is not None:
            images.append(normalized)

    return {
        "id": product.get("id"),
        "handle": product.get("handle"),
        "title": product.get("title"),
        "vendor": product.get("vendor"),
        "product_type": product.get("product_type"),
        "tags": product.get("tags") or [],
        "published_at": product.get("published_at"),
        "created_at": product.get("created_at"),
        "updated_at": product.get("updated_at"),
        "images": images,
        "variants": variants
    }


# ============================================================
# Product identity
# ============================================================

def product_key(product):
    product_id = product.get("id")

    if product_id is not None:
        return str(product_id)

    handle = product.get("handle")

    if handle:
        return str(handle)

    return sha256(
        json.dumps(
            product,
            ensure_ascii=False,
            sort_keys=True
        )
    )


# ============================================================
# Product comparison
# ============================================================

def product_signature(product):
    return sha256(
        json.dumps(
            product,
            ensure_ascii=False,
            sort_keys=True
        )
    )


def get_skus(product):
    result = []

    for variant in product.get("variants") or []:
        if not isinstance(variant, dict):
            continue

        sku = variant.get("sku")

        if sku and sku not in result:
            result.append(str(sku))

    return result


# ============================================================
# History
# ============================================================

def create_history_entry(product, visible=True):
    timestamp = now_iso()

    return {
        "id": product_key(product),
        "title": product.get("title"),
        "handle": product.get("handle"),

        "first_seen": timestamp,
        "last_seen": timestamp,

        "first_visible": visible,
        "last_visible": visible,

        "first_state": product,
        "last_state": product,

        "change_count": 0
    }


def update_history(history, product, visible):
    key = product_key(product)

    timestamp = now_iso()

    if key not in history:
        history[key] = create_history_entry(
            product,
            visible
        )

        return "added"

    item = history[key]

    previous_state = item.get("last_state")

    changed = (
        product_signature(previous_state or {})
        != product_signature(product)
    )

    previous_visible = item.get(
        "last_visible",
        True
    )

    item["last_seen"] = timestamp
    item["last_visible"] = visible
    item["last_state"] = product

    if changed or previous_visible != visible:
        item["change_count"] = (
            int(item.get("change_count", 0)) + 1
        )

    if not previous_visible and visible:
        return "restored"

    if previous_visible and not visible:
        return "hidden"

    if changed:
        return "changed"

    return "unchanged"


# ============================================================
# CSV
# ============================================================

def write_products_csv(path, products):
    path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "id",
        "title",
        "handle",
        "sku",
        "variant_title",
        "price",
        "compare_at_price",
        "available",
        "variant_created_at",
        "variant_updated_at",
        "product_created_at",
        "product_updated_at",
        "tags",
        "image"
    ]

    with path.open(
        "w",
        encoding="utf-8-sig",
        newline=""
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=fieldnames
        )

        writer.writeheader()

        for product in products:
            variants = product.get("variants") or [{}]

            image = ""

            images = product.get("images") or []

            if images:
                image = images[0].get("src", "")

            for variant in variants:
                writer.writerow({
                    "id": product.get("id"),
                    "title": product.get("title"),
                    "handle": product.get("handle"),
                    "sku": variant.get("sku"),
                    "variant_title": variant.get("title"),
                    "price": variant.get("price"),
                    "compare_at_price": variant.get(
                        "compare_at_price"
                    ),
                    "available": variant.get("available"),
                    "variant_created_at": variant.get(
                        "created_at"
                    ),
                    "variant_updated_at": variant.get(
                        "updated_at"
                    ),
                    "product_created_at": product.get(
                        "created_at"
                    ),
                    "product_updated_at": product.get(
                        "updated_at"
                    ),
                    "tags": ", ".join(
                        product.get("tags") or []
                    ),
                    "image": image
                })


def write_history_csv(path, history):
    path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "id",
        "title",
        "handle",
        "first_seen",
        "last_seen",
        "first_visible",
        "last_visible",
        "change_count",
        "first_skus",
        "last_skus"
    ]

    with path.open(
        "w",
        encoding="utf-8-sig",
        newline=""
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=fieldnames
        )

        writer.writeheader()

        for item in history.values():

            first_state = item.get(
                "first_state"
            ) or {}

            last_state = item.get(
                "last_state"
            ) or {}

            writer.writerow({
                "id": item.get("id"),
                "title": item.get("title"),
                "handle": item.get("handle"),
                "first_seen": item.get("first_seen"),
                "last_seen": item.get("last_seen"),
                "first_visible": item.get(
                    "first_visible"
                ),
                "last_visible": item.get(
                    "last_visible"
                ),
                "change_count": item.get(
                    "change_count"
                ),
                "first_skus": ", ".join(
                    get_skus(first_state)
                ),
                "last_skus": ", ".join(
                    get_skus(last_state)
                )
            })


# ============================================================
# Monitor
# ============================================================

def monitor_one(config):
    collection = config["collection"]
    name = config.get("name", collection)
    url = config["url"]

    pages = config.get("pages", [1])

    if not isinstance(pages, list):
        raise RuntimeError(
            f"{collection}: pages must be a list"
        )

    collection_dir = DATA_DIR / safe_filename(collection)
    docs_collection_dir = (
        DOCS_DATA_DIR / safe_filename(collection)
    )

    collection_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    docs_collection_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    products_json = (
        collection_dir / "products.json"
    )

    products_csv = (
        collection_dir / "products.csv"
    )

    history_json = (
        collection_dir / "history.json"
    )

    history_csv = (
        collection_dir / "history.csv"
    )

    previous_products = load_json(
        products_json,
        []
    )

    previous_history = load_json(
        history_json,
        {}
    )

    if not isinstance(previous_products, list):
        previous_products = []

    if not isinstance(previous_history, dict):
        previous_history = {}

    print()
    print("=" * 60)
    print(f"Monitoring: {name}")
    print(f"Collection: {collection}")
    print(f"Pages: {pages}")
    print("=" * 60)

    all_products = []

    for page in pages:

        try:
            page_products = fetch_page(
                url,
                page
            )

        except Exception as e:
            raise RuntimeError(
                f"{name} page={page}: {e}"
            )

        print(
            f"[{name}] page={page}: "
            f"{len(page_products)} products"
        )

        for product in page_products:

            normalized = normalize_product(
                product
            )

            if normalized is not None:
                all_products.append(
                    normalized
                )

        time.sleep(REQUEST_INTERVAL)

    # --------------------------------------------------------
    # Safety checks
    # --------------------------------------------------------

    if len(all_products) == 0:

        if previous_products:
            raise RuntimeError(
                f"{name}: empty result. "
                f"Previous count={len(previous_products)}. "
                f"Data update aborted."
            )

        print(
            f"[{name}] first run returned 0 products."
        )

    previous_count = len(previous_products)
    current_count = len(all_products)

    if previous_count > 0:

        ratio = current_count / previous_count

        print(
            f"[{name}] previous={previous_count}, "
            f"current={current_count}, "
            f"ratio={ratio:.2f}"
        )

        if ratio < MIN_COUNT_RATIO:

            raise RuntimeError(
                f"{name}: product count dropped "
                f"too much. "
                f"Previous={previous_count}, "
                f"Current={current_count}, "
                f"Ratio={ratio:.2f}. "
                f"Data update aborted."
            )

    # --------------------------------------------------------
    # Deduplicate
    # --------------------------------------------------------

    unique = {}

    for product in all_products:

        key = product_key(product)

        unique[key] = product

    all_products = list(
        unique.values()
    )

    # --------------------------------------------------------
    # Current product map
    # --------------------------------------------------------

    current_map = {
        product_key(product): product
        for product in all_products
    }

    previous_map = {
        product_key(product): product
        for product in previous_products
    }

    added = []
    removed = []
    changed = []

    for key, product in current_map.items():

        if key not in previous_map:
            added.append(product)

        else:
            if (
                product_signature(product)
                != product_signature(
                    previous_map[key]
                )
            ):
                changed.append(product)

    for key, product in previous_map.items():

        if key not in current_map:
            removed.append(product)

    # --------------------------------------------------------
    # History
    # --------------------------------------------------------

    for product in all_products:

        update_history(
            previous_history,
            product,
            True
        )

    # 非表示になった商品
    for product in removed:

        update_history(
            previous_history,
            product,
            False
        )

        key = product_key(product)

        if key in previous_history:

            previous_history[key][
                "last_visible"
            ] = False

    # --------------------------------------------------------
    # Save current JSON / CSV
    # --------------------------------------------------------

    save_json(
        products_json,
        all_products
    )

    write_products_csv(
        products_csv,
        all_products
    )

    save_json(
        history_json,
        previous_history
    )

    write_history_csv(
        history_csv,
        previous_history
    )

    # --------------------------------------------------------
    # Copy to GitHub Pages
    # --------------------------------------------------------

    docs_products_json = (
        docs_collection_dir /
        "products.json"
    )

    docs_history_json = (
        docs_collection_dir /
        "history.json"
    )

    shutil.copy2(
        products_json,
        docs_products_json
    )

    shutil.copy2(
        history_json,
        docs_history_json
    )

    # --------------------------------------------------------
    # Discord
    # --------------------------------------------------------

    if added or removed or changed:

        lines = [
            f"📦 **{name}**",
            ""
        ]

        if added:

            lines.append(
                f"🟢 **追加: {len(added)}件**"
            )

            for product in added[:20]:

                skus = get_skus(product)

                sku_text = (
                    ", ".join(skus)
                    if skus
                    else "SKUなし"
                )

                lines.append(
                    f"- {product.get('title')}"
                    f" (`{sku_text}`)"
                )

        if removed:

            lines.append(
                f"🔴 **非表示: {len(removed)}件**"
            )

            for product in removed[:20]:

                skus = get_skus(product)

                sku_text = (
                    ", ".join(skus)
                    if skus
                    else "SKUなし"
                )

                lines.append(
                    f"- {product.get('title')}"
                    f" (`{sku_text}`)"
                )

        if changed:

            lines.append(
                f"🟡 **変更: {len(changed)}件**"
            )

            for product in changed[:20]:

                skus = get_skus(product)

                sku_text = (
                    ", ".join(skus)
                    if skus
                    else "SKUなし"
                )

                lines.append(
                    f"- {product.get('title')}"
                    f" (`{sku_text}`)"
                )

        send_discord(
            "\n".join(lines)
        )

    else:

        print(
            f"[{name}] No changes."
        )

    print()
    print(
        f"[{name}] "
        f"current={len(all_products)} "
        f"added={len(added)} "
        f"removed={len(removed)} "
        f"changed={len(changed)}"
    )

    return {
        "name": name,
        "collection": collection,
        "count": len(all_products),
        "added": len(added),
        "removed": len(removed),
        "changed": len(changed)
    }


# ============================================================
# Main
# ============================================================

def main():

    if not CONFIG_FILE.exists():

        send_error(
            f"Configuration file not found: "
            f"{CONFIG_FILE}"
        )

        sys.exit(1)

    config = load_json(
        CONFIG_FILE,
        {}
    )

    if not isinstance(config, dict):

        send_error(
            "monitors.json root must be an object."
        )

        sys.exit(1)

    monitors = config.get("monitors")

    if not isinstance(monitors, list):

        send_error(
            "monitors.json must contain "
            "'monitors' array."
        )

        sys.exit(1)

    results = []

    try:

        for monitor in monitors:

            if not isinstance(monitor, dict):

                raise RuntimeError(
                    "monitor entry must be an object"
                )

            required = [
                "collection",
                "url"
            ]

            for key in required:

                if not monitor.get(key):

                    raise RuntimeError(
                        f"monitor is missing "
                        f"'{key}'"
                    )

            result = monitor_one(
                monitor
            )

            results.append(result)

    except Exception as e:

        send_error(
            str(e)
        )

        sys.exit(1)

    print()
    print("=" * 60)
    print("All monitors completed successfully.")
    print("=" * 60)

    for result in results:

        print(
            f"{result['name']}: "
            f"{result['count']} products, "
            f"+{result['added']}, "
            f"-{result['removed']}, "
            f"changed={result['changed']}"
        )


if __name__ == "__main__":
    main()