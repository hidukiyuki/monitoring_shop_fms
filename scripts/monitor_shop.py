import csv
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests


# ============================================================
# 設定
# ============================================================

BASE_URL = (
    "https://findmestore.thinkr.jp/"
    "collections/isekaijoucho/products.json"
)

# 監視するページ
PAGES = [1, 2]

STORE_BASE = "https://findmestore.thinkr.jp"

DATA_DIR = Path("data")

PRODUCTS_JSON = DATA_DIR / "products.json"
PRODUCTS_CSV = DATA_DIR / "products.csv"

HISTORY_JSON = DATA_DIR / "history.json"
HISTORY_CSV = DATA_DIR / "history.csv"

# GitHub Pages
PAGES_DATA_DIR = Path("docs/data")

PAGES_PRODUCTS_JSON = (
    PAGES_DATA_DIR / "products.json"
)

PAGES_HISTORY_JSON = (
    PAGES_DATA_DIR / "history.json"
)

# 前回件数に対して50%未満になったら異常扱い
MIN_COUNT_RATIO = 0.5

TIMEOUT = 30

DISCORD_WEBHOOK = os.environ.get(
    "DISCORD_WEBHOOK",
    "",
)


# ============================================================
# Utility
# ============================================================

def now_iso():
    return datetime.now(
        timezone.utc
    ).isoformat()


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


def load_json(path, default):
    if not path.exists():
        return default

    if path.stat().st_size == 0:
        print(
            f"{path} is empty. "
            "Using default value."
        )
        return default

    try:
        with path.open(
            "r",
            encoding="utf-8",
        ) as f:
            content = f.read().strip()

        if not content:
            return default

        return json.loads(content)

    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"Invalid JSON: {path}: {e}"
        )


# ============================================================
# Shopify API
# ============================================================

def fetch_page(page):
    print(
        f"Fetching page={page}"
    )

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
                "(compatible; "
                "FINDME-Store-Monitor/1.0)"
            ),
            "Accept": "application/json",
        },
    )

    response.raise_for_status()

    if not response.content:
        raise RuntimeError(
            f"page={page}: empty response"
        )

    try:
        data = response.json()

    except ValueError as e:
        raise RuntimeError(
            f"page={page}: invalid JSON: {e}"
        )

    if not isinstance(data, dict):
        raise RuntimeError(
            f"page={page}: "
            "response is not an object"
        )

    products = data.get(
        "products"
    )

    if not isinstance(products, list):
        raise RuntimeError(
            f"page={page}: "
            "products is not a list"
        )

    if not products:
        raise RuntimeError(
            f"page={page}: "
            "empty products list"
        )

    print(
        f"page={page}: "
        f"{len(products)} products"
    )

    return products


# ============================================================
# Product normalization
# ============================================================

def normalize_product(product):
    if not isinstance(
        product,
        dict,
    ):
        return None

    handle = product.get(
        "handle"
    )

    if not handle:
        return None

    variants = []

    for variant in (
        product.get("variants")
        or []
    ):
        if not isinstance(
            variant,
            dict,
        ):
            continue

        variants.append(
            {
                "id": variant.get(
                    "id"
                ),
                "title": variant.get(
                    "title"
                ),
                "price": variant.get(
                    "price"
                ),
                "available": variant.get(
                    "available"
                ),
            }
        )

    images = []

    for image in (
        product.get("images")
        or []
    ):
        if not isinstance(
            image,
            dict,
        ):
            continue

        src = image.get(
            "src"
        )

        if src:
            images.append(src)

    tags = product.get(
        "tags"
    ) or []

    if not isinstance(
        tags,
        list,
    ):
        tags = []

    tags = sorted(
        str(tag)
        for tag in tags
    )

    return {
        "id": product.get(
            "id"
        ),
        "handle": handle,
        "title": product.get(
            "title"
        ),
        "vendor": product.get(
            "vendor"
        ),
        "product_type": product.get(
            "product_type"
        ),
        "tags": tags,
        "url": (
            f"{STORE_BASE}"
            f"/products/{handle}"
        ),
        "images": images,
        "variants": variants,
    }


def build_products(raw_products):
    products = {}

    for raw in raw_products:

        product = normalize_product(
            raw
        )

        if product is None:
            continue

        products[
            product["handle"]
        ] = product

    return dict(
        sorted(
            products.items()
        )
    )


# ============================================================
# Product state
# ============================================================

def get_price(product):
    prices = []

    for variant in (
        product.get("variants")
        or []
    ):
        try:
            value = float(
                variant.get("price")
            )

            prices.append(value)

        except (
            TypeError,
            ValueError,
        ):
            pass

    if not prices:
        return None

    return min(prices)


def get_price_text(product):
    value = get_price(
        product
    )

    if value is None:
        return ""

    if value.is_integer():
        return str(
            int(value)
        )

    return str(value)


def is_available(product):
    return any(
        variant.get(
            "available"
        ) is True
        for variant in (
            product.get(
                "variants"
            )
            or []
        )
    )


def get_state(product):
    return {
        "price": get_price_text(
            product
        ),
        "available": is_available(
            product
        ),
        "tags": list(
            product.get(
                "tags"
            )
            or []
        ),
        "images": list(
            product.get(
                "images"
            )
            or []
        ),
    }


# ============================================================
# History
#
# 初回状態 first
# 最終状態 last
# ============================================================

def create_history_record(
    product,
    timestamp,
):
    return {
        "handle": product.get(
            "handle"
        ),
        "title": product.get(
            "title"
        ),
        "url": product.get(
            "url"
        ),

        "first_seen": timestamp,
        "last_seen": timestamp,

        "visible": True,

        "first": get_state(
            product
        ),

        "last": get_state(
            product
        ),
    }


def update_history_record(
    record,
    product,
    timestamp,
):
    record["title"] = product.get(
        "title"
    )

    record["url"] = product.get(
        "url"
    )

    record["last_seen"] = timestamp

    record["visible"] = True

    record["last"] = get_state(
        product
    )

    return record


def mark_hidden(
    record,
):
    record["visible"] = False

    return record


# ============================================================
# Difference
# ============================================================

def compare_products(
    previous,
    current,
):
    previous_keys = set(
        previous.keys()
    )

    current_keys = set(
        current.keys()
    )

    added = sorted(
        current_keys
        - previous_keys
    )

    removed = sorted(
        previous_keys
        - current_keys
    )

    stock_changed = []
    price_changed = []

    for handle in sorted(
        previous_keys
        & current_keys
    ):

        old = get_state(
            previous[handle]
        )

        new = get_state(
            current[handle]
        )

        if (
            old["available"]
            != new["available"]
        ):
            stock_changed.append(
                handle
            )

        if (
            old["price"]
            != new["price"]
        ):
            price_changed.append(
                handle
            )

    return {
        "added": added,
        "removed": removed,
        "stock_changed":
            stock_changed,
        "price_changed":
            price_changed,
    }


# ============================================================
# CSV
# ============================================================

def save_products_csv(
    products
):
    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    rows = []

    for product in (
        products.values()
    ):
        rows.append(
            {
                "handle":
                    product.get(
                        "handle",
                        "",
                    ),

                "title":
                    product.get(
                        "title",
                        "",
                    ),

                "price":
                    get_price_text(
                        product
                    ),

                "available":
                    is_available(
                        product
                    ),

                "tags":
                    ", ".join(
                        product.get(
                            "tags"
                        )
                        or []
                    ),

                "url":
                    product.get(
                        "url",
                        "",
                    ),

                "image":
                    (
                        product.get(
                            "images"
                        )
                        or [""]
                    )[0],
            }
        )

    with PRODUCTS_CSV.open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as f:

        fieldnames = [
            "handle",
            "title",
            "price",
            "available",
            "tags",
            "url",
            "image",
        ]

        writer = csv.DictWriter(
            f,
            fieldnames=fieldnames,
        )

        writer.writeheader()
        writer.writerows(rows)


def save_history_csv(
    history
):
    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    rows = []

    for record in (
        history.values()
    ):
        first = record.get(
            "first"
        ) or {}

        last = record.get(
            "last"
        ) or {}

        rows.append(
            {
                "handle":
                    record.get(
                        "handle",
                        "",
                    ),

                "title":
                    record.get(
                        "title",
                        "",
                    ),

                "visible":
                    record.get(
                        "visible",
                        False,
                    ),

                "first_seen":
                    record.get(
                        "first_seen",
                        "",
                    ),

                "last_seen":
                    record.get(
                        "last_seen",
                        "",
                    ),

                "first_price":
                    first.get(
                        "price",
                        "",
                    ),

                "last_price":
                    last.get(
                        "price",
                        "",
                    ),

                "first_available":
                    first.get(
                        "available",
                        False,
                    ),

                "last_available":
                    last.get(
                        "available",
                        False,
                    ),

                "first_tags":
                    ", ".join(
                        first.get(
                            "tags"
                        )
                        or []
                    ),

                "last_tags":
                    ", ".join(
                        last.get(
                            "tags"
                        )
                        or []
                    ),

                "url":
                    record.get(
                        "url",
                        "",
                    ),
            }
        )

    with HISTORY_CSV.open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as f:

        fieldnames = [
            "handle",
            "title",
            "visible",
            "first_seen",
            "last_seen",
            "first_price",
            "last_price",
            "first_available",
            "last_available",
            "first_tags",
            "last_tags",
            "url",
        ]

        writer = csv.DictWriter(
            f,
            fieldnames=fieldnames,
        )

        writer.writeheader()
        writer.writerows(rows)


# ============================================================
# Discord
# ============================================================

def send_discord(
    content
):
    if not DISCORD_WEBHOOK:
        print(
            "DISCORD_WEBHOOK "
            "is not configured."
        )
        return

    response = requests.post(
        DISCORD_WEBHOOK,
        json={
            "content": content
        },
        timeout=30,
    )

    response.raise_for_status()


def send_error(
    message
):
    print(
        message,
        file=sys.stderr,
    )

    if not DISCORD_WEBHOOK:
        return

    try:
        send_discord(
            "🚨 **FINDME STORE監視エラー**\n\n"
            + message
        )

    except Exception as e:
        print(
            "Failed to send "
            f"Discord error: {e}",
            file=sys.stderr,
        )


def product_status(
    product
):
    return (
        "販売中"
        if is_available(product)
        else "Sold out"
    )


def product_line(
    product
):
    price = get_price_text(
        product
    )

    if price:
        price += "円"
    else:
        price = "価格不明"

    return (
        f"・{product.get('title', '(no title)')}"
        f" | {price}"
        f" | {product_status(product)}"
    )


def build_notification(
    previous,
    current,
    diff,
):
    sections = []

    # --------------------------------------------------------
    # 追加
    # --------------------------------------------------------

    if diff["added"]:

        lines = [
            "🆕 **新商品**"
        ]

        for handle in diff[
            "added"
        ]:

            product = current[
                handle
            ]

            lines.append(
                product_line(
                    product
                )
            )

            lines.append(
                product.get(
                    "url",
                    "",
                )
            )

        sections.append(
            "\n".join(lines)
        )

    # --------------------------------------------------------
    # 削除 / 非表示
    # --------------------------------------------------------

    if diff["removed"]:

        lines = [
            "🗑️ **非表示になった商品**"
        ]

        for handle in diff[
            "removed"
        ]:

            product = previous[
                handle
            ]

            lines.append(
                f"・{product.get('title', handle)}"
            )

            lines.append(
                product.get(
                    "url",
                    "",
                )
            )

        sections.append(
            "\n".join(lines)
        )

    # --------------------------------------------------------
    # 在庫
    # --------------------------------------------------------

    if diff[
        "stock_changed"
    ]:

        lines = [
            "📦 **在庫変更**"
        ]

        for handle in diff[
            "stock_changed"
        ]:

            old = previous[
                handle
            ]

            new = current[
                handle
            ]

            old_status = (
                "販売中"
                if is_available(old)
                else "Sold out"
            )

            new_status = (
                "販売中"
                if is_available(new)
                else "Sold out"
            )

            lines.append(
                f"・{new.get('title', handle)}"
            )

            lines.append(
                f"  {old_status} → {new_status}"
            )

            lines.append(
                new.get(
                    "url",
                    "",
                )
            )

        sections.append(
            "\n".join(lines)
        )

    # --------------------------------------------------------
    # 価格
    # --------------------------------------------------------

    if diff[
        "price_changed"
    ]:

        lines = [
            "💰 **価格変更**"
        ]

        for handle in diff[
            "price_changed"
        ]:

            old = previous[
                handle
            ]

            new = current[
                handle
            ]

            old_price = (
                get_price_text(old)
                or "不明"
            )

            new_price = (
                get_price_text(new)
                or "不明"
            )

            lines.append(
                f"・{new.get('title', handle)}"
            )

            lines.append(
                f"  {old_price}円 → "
                f"{new_price}円"
            )

            lines.append(
                new.get(
                    "url",
                    "",
                )
            )

        sections.append(
            "\n".join(lines)
        )

    if not sections:
        return None

    return (
        "🛒 **FINDME STORE / ヰ世界情緒**\n\n"
        + "\n\n".join(sections)
    )


# ============================================================
# Main
# ============================================================

def main():

    timestamp = now_iso()

    try:

        # ----------------------------------------------------
        # API取得
        # ----------------------------------------------------

        raw_products = []

        for page in PAGES:

            page_products = fetch_page(
                page
            )

            raw_products.extend(
                page_products
            )

        # ----------------------------------------------------
        # 正規化
        # ----------------------------------------------------

        current = build_products(
            raw_products
        )

        current_count = len(
            current
        )

        print(
            f"Current products: "
            f"{current_count}"
        )

        if current_count == 0:
            raise RuntimeError(
                "Current product count "
                "is zero."
            )

        # ----------------------------------------------------
        # 前回データ
        # ----------------------------------------------------

        previous = load_json(
            PRODUCTS_JSON,
            {},
        )

        if not isinstance(
            previous,
            dict,
        ):
            raise RuntimeError(
                "products.json "
                "must be an object."
            )

        previous_count = len(
            previous
        )

        print(
            f"Previous products: "
            f"{previous_count}"
        )

        # ----------------------------------------------------
        # 初回
        # ----------------------------------------------------

        if previous_count == 0:

            history = load_json(
                HISTORY_JSON,
                {},
            )

            if not isinstance(
                history,
                dict,
            ):
                history = {}

            for handle, product in (
                current.items()
            ):

                history[
                    handle
                ] = create_history_record(
                    product,
                    timestamp,
                )

            atomic_write_json(
                PRODUCTS_JSON,
                current,
            )

            atomic_write_json(
                HISTORY_JSON,
                history,
            )

            save_products_csv(
                current
            )

            save_history_csv(
                history
            )

            atomic_write_json(
                PAGES_PRODUCTS_JSON,
                {
                    "updated_at":
                        timestamp,
                    "product_count":
                        current_count,
                    "products":
                        current,
                },
            )

            atomic_write_json(
                PAGES_HISTORY_JSON,
                {
                    "updated_at":
                        timestamp,
                    "history_count":
                        len(history),
                    "history":
                        history,
                },
            )

            send_discord(
                "📌 **FINDME STORE監視を開始しました**\n\n"
                f"取得商品数: {current_count}\n"
                "今回は初回取得のため、"
                "差分通知はありません。"
            )

            return

        # ----------------------------------------------------
        # 件数激減チェック
        # ----------------------------------------------------

        minimum_count = max(
            1,
            int(
                previous_count
                * MIN_COUNT_RATIO
            ),
        )

        if (
            current_count
            < minimum_count
        ):

            raise RuntimeError(
                "商品数が異常に減少しました。\n"
                f"前回: {previous_count}\n"
                f"今回: {current_count}\n"
                f"許容最低値: {minimum_count}\n"
                "安全のためデータを更新しません。"
            )

        # ----------------------------------------------------
        # 差分
        # ----------------------------------------------------

        diff = compare_products(
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
        # History
        # ----------------------------------------------------

        history = load_json(
            HISTORY_JSON,
            {},
        )

        if not isinstance(
            history,
            dict,
        ):
            raise RuntimeError(
                "history.json "
                "must be an object."
            )

        # 現在存在する商品
        for handle, product in (
            current.items()
        ):

            if handle in history:

                history[
                    handle
                ] = update_history_record(
                    history[handle],
                    product,
                    timestamp,
                )

            else:

                history[
                    handle
                ] = create_history_record(
                    product,
                    timestamp,
                )

        # 今回消えた商品
        for handle in diff[
            "removed"
        ]:

            if handle in history:

                history[
                    handle
                ] = mark_hidden(
                    history[handle]
                )

        # ----------------------------------------------------
        # Save
        # ----------------------------------------------------

        atomic_write_json(
            PRODUCTS_JSON,
            current,
        )

        atomic_write_json(
            HISTORY_JSON,
            history,
        )

        save_products_csv(
            current
        )

        save_history_csv(
            history
        )

        # ----------------------------------------------------
        # GitHub Pages
        # ----------------------------------------------------

        atomic_write_json(
            PAGES_PRODUCTS_JSON,
            {
                "updated_at":
                    timestamp,
                "product_count":
                    current_count,
                "products":
                    current,
            },
        )

        atomic_write_json(
            PAGES_HISTORY_JSON,
            {
                "updated_at":
                    timestamp,
                "history_count":
                    len(history),
                "history":
                    history,
            },
        )

        # ----------------------------------------------------
        # Discord
        # ----------------------------------------------------

        notification = (
            build_notification(
                previous,
                current,
                diff,
            )
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