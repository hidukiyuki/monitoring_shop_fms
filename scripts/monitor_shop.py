import csv
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests


# ============================================================
# Settings
# ============================================================

BASE_URL = (
    "https://findmestore.thinkr.jp/"
    "collections/isekaijoucho/products.json"
)

PAGES = [1, 2]

STORE_BASE = "https://findmestore.thinkr.jp"

DATA_DIR = Path("data")

JSON_PATH = DATA_DIR / "products.json"
CSV_PATH = DATA_DIR / "products.csv"

# GitHub Pages用
PAGES_DATA_DIR = Path("docs/data")
PAGES_JSON_PATH = PAGES_DATA_DIR / "products.json"

# 前回の商品数に対して、これ未満になったら異常扱い
MIN_COUNT_RATIO = 0.5

TIMEOUT = 30

DISCORD_WEBHOOK = os.environ.get(
    "DISCORD_WEBHOOK",
    "",
)


# ============================================================
# HTTP
# ============================================================

def fetch_page(page):
    print(f"Fetching page={page}")

    response = requests.get(
        BASE_URL,
        params={
            "limit": 250,
            "page": page,
        },
        timeout=TIMEOUT,
        headers={
            "User-Agent": (
                "Mozilla/5.0 "
                "(compatible; FINDME-Store-Monitor/1.0)"
            ),
            "Accept": "application/json",
        },
    )

    response.raise_for_status()

    if not response.content:
        raise RuntimeError(
            f"page={page}: empty HTTP response"
        )

    try:
        data = response.json()
    except ValueError as e:
        raise RuntimeError(
            f"page={page}: invalid JSON response: {e}"
        )

    if not isinstance(data, dict):
        raise RuntimeError(
            f"page={page}: response is not an object"
        )

    products = data.get("products")

    if not isinstance(products, list):
        raise RuntimeError(
            f"page={page}: products is not a list"
        )

    if len(products) == 0:
        raise RuntimeError(
            f"page={page}: empty products response"
        )

    print(
        f"page={page}: "
        f"{len(products)} products"
    )

    return products


# ============================================================
# Normalize
# ============================================================

def normalize_product(product):
    if not isinstance(product, dict):
        return None

    handle = product.get("handle")

    if not handle:
        return None

    variants = product.get("variants") or []

    normalized_variants = []

    for variant in variants:
        if not isinstance(variant, dict):
            continue

        normalized_variants.append(
            {
                "id": variant.get("id"),
                "title": variant.get("title"),
                "price": variant.get("price"),
                "available": variant.get("available"),
            }
        )

    images = []

    for image in product.get("images") or []:
        if not isinstance(image, dict):
            continue

        src = image.get("src")

        if src:
            images.append(src)

    tags = product.get("tags") or []

    if not isinstance(tags, list):
        tags = []

    tags = [
        str(tag)
        for tag in tags
    ]

    return {
        "id": product.get("id"),
        "handle": handle,
        "title": product.get("title"),
        "vendor": product.get("vendor"),
        "product_type": product.get("product_type"),
        "tags": sorted(tags),
        "url": (
            f"{STORE_BASE}/products/{handle}"
        ),
        "images": images,
        "variants": normalized_variants,
    }


def build_products(raw_products):
    products = {}

    for raw in raw_products:
        normalized = normalize_product(raw)

        if normalized is None:
            continue

        handle = normalized["handle"]

        # handleをキーとして重複排除
        products[handle] = normalized

    return dict(
        sorted(products.items())
    )


# ============================================================
# Previous data
# ============================================================

def load_previous():
    if not JSON_PATH.exists():
        print(
            "No previous JSON. "
            "Treating as first run."
        )
        return {}

    # 空ファイル
    if JSON_PATH.stat().st_size == 0:
        print(
            "Previous JSON is empty. "
            "Treating as first run."
        )
        return {}

    try:
        with JSON_PATH.open(
            "r",
            encoding="utf-8",
        ) as f:
            content = f.read().strip()

        if not content:
            print(
                "Previous JSON contains no data. "
                "Treating as first run."
            )
            return {}

        data = json.loads(content)

        if not isinstance(data, dict):
            raise RuntimeError(
                "previous JSON is not an object"
            )

        return data

    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"previous JSON is invalid JSON: {e}"
        )

    except Exception as e:
        raise RuntimeError(
            f"failed to read previous JSON: {e}"
        )


# ============================================================
# Save JSON
# ============================================================

def atomic_write_json(path, data):
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temp_path = path.with_suffix(
        path.suffix + ".tmp"
    )

    with temp_path.open(
        "w",
        encoding="utf-8",
    ) as f:
        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )

        f.write("\n")

    temp_path.replace(path)


def save_json(products):
    atomic_write_json(
        JSON_PATH,
        products,
    )


def save_pages_json(products):
    atomic_write_json(
        PAGES_JSON_PATH,
        {
            "updated_at": datetime.now(
                timezone.utc
            ).isoformat(),
            "product_count": len(products),
            "products": products,
        },
    )


# ============================================================
# CSV
# ============================================================

def variant_summary(product):
    variants = product.get("variants") or []

    if not variants:
        return {
            "price": "",
            "available": False,
        }

    prices = []
    available = False

    for variant in variants:
        if not isinstance(variant, dict):
            continue

        value = variant.get("price")

        if value is not None:
            prices.append(
                str(value)
            )

        if variant.get("available") is True:
            available = True

    unique_prices = sorted(
        set(prices)
    )

    if len(unique_prices) == 1:
        price = unique_prices[0]
    elif unique_prices:
        price = " / ".join(
            unique_prices
        )
    else:
        price = ""

    return {
        "price": price,
        "available": available,
    }


def product_row(product):
    summary = variant_summary(
        product
    )

    images = product.get("images") or []

    return {
        "handle": product.get(
            "handle",
            "",
        ),
        "title": product.get(
            "title",
            "",
        ),
        "price": summary["price"],
        "available": summary[
            "available"
        ],
        "tags": ", ".join(
            product.get("tags") or []
        ),
        "url": product.get(
            "url",
            "",
        ),
        "image": (
            images[0]
            if images
            else ""
        ),
    }


def save_csv(products):
    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    fieldnames = [
        "handle",
        "title",
        "price",
        "available",
        "tags",
        "url",
        "image",
    ]

    rows = [
        product_row(product)
        for product in products.values()
    ]

    with CSV_PATH.open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=fieldnames,
        )

        writer.writeheader()
        writer.writerows(rows)


# ============================================================
# Comparison
# ============================================================

def compare(previous, current):
    previous_keys = set(
        previous.keys()
    )

    current_keys = set(
        current.keys()
    )

    added = sorted(
        current_keys - previous_keys
    )

    removed = sorted(
        previous_keys - current_keys
    )

    stock_changed = []
    price_changed = []
    changed = []

    for handle in sorted(
        previous_keys & current_keys
    ):
        old = previous[handle]
        new = current[handle]

        old_summary = variant_summary(
            old
        )

        new_summary = variant_summary(
            new
        )

        if (
            old_summary["available"]
            != new_summary["available"]
        ):
            stock_changed.append(
                handle
            )

        if (
            old_summary["price"]
            != new_summary["price"]
        ):
            price_changed.append(
                handle
            )

        if old != new:
            changed.append(
                handle
            )

    return {
        "added": added,
        "removed": removed,
        "stock_changed": stock_changed,
        "price_changed": price_changed,
        "changed": changed,
    }


# ============================================================
# Display
# ============================================================

def status(product):
    if variant_summary(
        product
    )["available"]:
        return "販売中"

    return "Sold out"


def price(product):
    return variant_summary(
        product
    )["price"]


def product_line(product):
    price_text = price(product)

    if price_text:
        price_text += "円"
    else:
        price_text = "価格不明"

    return (
        f"・{product.get('title', '(no title)')} "
        f"| {price_text} "
        f"| {status(product)}"
    )


# ============================================================
# Discord
# ============================================================

def send_discord(content):
    if not DISCORD_WEBHOOK:
        print(
            "DISCORD_WEBHOOK is not configured."
        )
        return

    # Discord content上限を考慮
    if len(content) > 1900:
        content = (
            content[:1850]
            + "\n\n…以下省略"
        )

    response = requests.post(
        DISCORD_WEBHOOK,
        json={
            "content": content,
        },
        timeout=30,
    )

    response.raise_for_status()


def send_error(message):
    print(
        f"ERROR: {message}",
        file=sys.stderr,
    )

    if not DISCORD_WEBHOOK:
        return

    try:
        send_discord(
            "🚨 **FINDME STORE監視エラー**\n\n"
            f"{message}"
        )
    except Exception as e:
        print(
            "Discord error notification "
            f"failed: {e}",
            file=sys.stderr,
        )


def build_notification(
    previous,
    current,
    diff,
):
    messages = []

    added = diff["added"]
    removed = diff["removed"]
    stock_changed = diff[
        "stock_changed"
    ]
    price_changed = diff[
        "price_changed"
    ]

    # 新商品
    if added:
        messages.append(
            "🆕 **新商品**"
        )

        for handle in added:
            product = current[handle]

            messages.append(
                product_line(product)
            )

            messages.append(
                f"  {product.get('url', '')}"
            )

    # 削除
    if removed:
        messages.append(
            "\n🗑️ **商品削除**"
        )

        for handle in removed:
            product = previous[handle]

            messages.append(
                f"・{product.get('title', handle)}"
            )

            messages.append(
                f"  {product.get('url', '')}"
            )

    # 在庫変更
    if stock_changed:
        messages.append(
            "\n📦 **在庫変更**"
        )

        for handle in stock_changed:
            old = previous[handle]
            new = current[handle]

            messages.append(
                f"・{new.get('title', handle)}"
            )

            messages.append(
                f"  {status(old)} → "
                f"{status(new)}"
            )

            messages.append(
                f"  {new.get('url', '')}"
            )

    # 価格変更
    if price_changed:
        messages.append(
            "\n💰 **価格変更**"
        )

        for handle in price_changed:
            old = previous[handle]
            new = current[handle]

            messages.append(
                f"・{new.get('title', handle)}"
            )

            messages.append(
                f"  {price(old) or '不明'} → "
                f"{price(new) or '不明'}"
            )

            messages.append(
                f"  {new.get('url', '')}"
            )

    if not messages:
        return None

    messages.insert(
        0,
        "🛒 **FINDME STORE / ヰ世界情緒**",
    )

    return "\n".join(messages)


# ============================================================
# Main
# ============================================================

def main():
    try:
        DATA_DIR.mkdir(
            parents=True,
            exist_ok=True,
        )

        # ----------------------------------------------------
        # Fetch all pages
        # ----------------------------------------------------

        raw_products = []

        for page in PAGES:
            page_products = fetch_page(page)

            if len(page_products) == 0:
                raise RuntimeError(
                    f"page={page}: "
                    "empty products response"
                )

            raw_products.extend(
                page_products
            )

        # ----------------------------------------------------
        # Normalize
        # ----------------------------------------------------

        current = build_products(
            raw_products
        )

        current_count = len(current)

        print(
            f"Total unique products: "
            f"{current_count}"
        )

        if current_count == 0:
            raise RuntimeError(
                "normalized product count "
                "is zero"
            )

        # ----------------------------------------------------
        # Load previous
        # ----------------------------------------------------

        previous = load_previous()

        previous_count = len(previous)

        print(
            f"Previous products: "
            f"{previous_count}"
        )

        # ----------------------------------------------------
        # First run
        # ----------------------------------------------------

        if previous_count == 0:
            print(
                "Creating initial snapshot."
            )

            save_json(current)
            save_csv(current)
            save_pages_json(current)

            send_discord(
                "📌 **FINDME STORE監視を開始しました**\n\n"
                f"取得商品数: {current_count}\n"
                "今回は初回取得のため、"
                "差分通知はありません。"
            )

            return

        # ----------------------------------------------------
        # Count safety check
        # ----------------------------------------------------

        minimum_count = max(
            1,
            int(
                previous_count
                * MIN_COUNT_RATIO
            ),
        )

        if current_count < minimum_count:
            raise RuntimeError(
                "商品数が異常に減少しました。\n"
                f"前回: {previous_count}\n"
                f"今回: {current_count}\n"
                f"許容最低値: {minimum_count}\n"
                "安全のため前回データを"
                "更新しません。"
            )

        # ----------------------------------------------------
        # Compare
        # ----------------------------------------------------

        diff = compare(
            previous,
            current,
        )

        print(
            f"Added: "
            f"{len(diff['added'])}"
        )

        print(
            f"Removed: "
            f"{len(diff['removed'])}"
        )

        print(
            f"Stock changed: "
            f"{len(diff['stock_changed'])}"
        )

        print(
            f"Price changed: "
            f"{len(diff['price_changed'])}"
        )

        # ----------------------------------------------------
        # Save
        # ----------------------------------------------------

        save_json(current)
        save_csv(current)
        save_pages_json(current)

        # ----------------------------------------------------
        # Discord
        # ----------------------------------------------------

        notification = build_notification(
            previous,
            current,
            diff,
        )

        if notification:
            send_discord(
                notification
            )
        else:
            print(
                "No changes."
            )

    except Exception as e:
        send_error(
            str(e)
        )

        sys.exit(1)


if __name__ == "__main__":
    main()