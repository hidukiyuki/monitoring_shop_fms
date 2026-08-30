import csv
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests


# ============================================================
# Paths
# ============================================================

CONFIG_FILE = Path("config/monitors.json")

DATA_DIR = Path("data")
DOCS_DATA_DIR = Path("docs/data")


# ============================================================
# Settings
# ============================================================

TIMEOUT = 30

# 前回件数に対して、この割合未満なら異常と判断
MIN_COUNT_RATIO = 0.5

DISCORD_WEBHOOK = os.environ.get(
    "DISCORD_WEBHOOK",
    ""
)

USER_AGENT = (
    "Mozilla/5.0 "
    "(compatible; FINDME/1.0)"
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
        exist_ok=True
    )

    tmp = path.with_suffix(
        path.suffix + ".tmp"
    )

    with tmp.open(
        "w",
        encoding="utf-8"
    ) as f:
        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2
        )
        f.write("\n")

    tmp.replace(path)


def load_json(path, default=None):
    if not path.exists():
        return default

    if path.stat().st_size == 0:
        raise RuntimeError(
            f"JSON is empty: {path}"
        )

    try:
        with path.open(
            "r",
            encoding="utf-8"
        ) as f:
            return json.load(f)

    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"Invalid JSON: {path}: {e}"
        )


# ============================================================
# Config
# ============================================================

def load_monitors():

    config = load_json(
        CONFIG_FILE
    )

    if not isinstance(
        config,
        dict
    ):
        raise RuntimeError(
            "monitors.json must be an object."
        )

    monitors = config.get(
        "monitors"
    )

    if not isinstance(
        monitors,
        list
    ) or not monitors:
        raise RuntimeError(
            '"monitors" must be a non-empty array.'
        )

    collections = set()

    for monitor in monitors:

        if not isinstance(
            monitor,
            dict
        ):
            raise RuntimeError(
                "Each monitor must be an object."
            )

        collection = monitor.get(
            "collection"
        )

        name = monitor.get(
            "name"
        )

        url = monitor.get(
            "url"
        )

        pages = monitor.get(
            "pages"
        )

        if not collection:
            raise RuntimeError(
                "collection is missing."
            )

        if collection in collections:
            raise RuntimeError(
                f"Duplicate collection: {collection}"
            )

        collections.add(
            collection
        )

        if not name:
            raise RuntimeError(
                f"{collection}: name is missing."
            )

        if not url:
            raise RuntimeError(
                f"{collection}: url is missing."
            )

        if (
            not isinstance(
                pages,
                list
            )
            or not pages
        ):
            raise RuntimeError(
                f"{collection}: pages must be a non-empty list."
            )

        for page in pages:

            if (
                not isinstance(
                    page,
                    int
                )
                or page < 1
            ):
                raise RuntimeError(
                    f"{collection}: invalid page: {page}"
                )

    return monitors


# ============================================================
# Shopify API
# ============================================================

def fetch_page(
    monitor,
    page
):

    name = monitor["name"]

    print(
        f"[{name}] GET page={page}"
    )

    response = requests.get(
        monitor["url"],
        params={
            "limit": 250,
            "page": page
        },
        timeout=TIMEOUT,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json"
        }
    )

    response.raise_for_status()

    if not response.content:
        raise RuntimeError(
            f"[{name}] page={page}: empty response"
        )

    try:
        data = response.json()
    except ValueError as e:
        raise RuntimeError(
            f"[{name}] page={page}: invalid JSON: {e}"
        )

    if not isinstance(
        data,
        dict
    ):
        raise RuntimeError(
            f"[{name}] page={page}: response is not object"
        )

    products = data.get(
        "products"
    )

    if not isinstance(
        products,
        list
    ):
        raise RuntimeError(
            f"[{name}] page={page}: products is not list"
        )

    if not products:
        raise RuntimeError(
            f"[{name}] page={page}: products is empty"
        )

    print(
        f"[{name}] page={page}: "
        f"{len(products)} products"
    )

    return products


# ============================================================
# Product normalization
# ============================================================

def normalize_product(product):

    if not isinstance(
        product,
        dict
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
            dict
        ):
            continue

        variants.append({
            "id": variant.get("id"),
            "title": variant.get("title"),
            "price": variant.get("price"),
            "available": variant.get("available")
        })

    images = []

    for image in (
        product.get("images")
        or []
    ):

        if not isinstance(
            image,
            dict
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
        list
    ):
        tags = []

    tags = sorted(
        str(tag)
        for tag in tags
    )

    return {
        "id": product.get("id"),
        "handle": handle,
        "title": product.get("title"),
        "vendor": product.get("vendor"),
        "product_type": product.get("product_type"),
        "tags": tags,
        "url": (
            "https://findmestore.thinkr.jp"
            f"/products/{handle}"
        ),
        "images": images,
        "variants": variants
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
            prices.append(
                float(
                    variant.get("price")
                )
            )
        except (
            TypeError,
            ValueError
        ):
            pass

    if not prices:
        return None

    return min(prices)


def get_price_text(product):

    price = get_price(
        product
    )

    if price is None:
        return ""

    if price.is_integer():
        return str(
            int(price)
        )

    return str(price)


def is_available(product):

    return any(
        variant.get(
            "available"
        ) is True

        for variant in (
            product.get("variants")
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
            product.get("tags")
            or []
        ),
        "images": list(
            product.get("images")
            or []
        )
    }


# ============================================================
# History
# ============================================================

def create_history(
    product,
    timestamp
):

    state = get_state(
        product
    )

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
        "first": state,
        "last": state
    }


def update_history(
    record,
    product,
    timestamp
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


# ============================================================
# Difference
# ============================================================

def compare(
    previous,
    current
):

    previous_keys = set(
        previous.keys()
    )

    current_keys = set(
        current.keys()
    )

    common = (
        previous_keys
        &
        current_keys
    )

    added = sorted(
        current_keys
        -
        previous_keys
    )

    removed = sorted(
        previous_keys
        -
        current_keys
    )

    stock_changed = sorted(
        handle
        for handle in common
        if (
            get_state(
                previous[handle]
            )["available"]
            !=
            get_state(
                current[handle]
            )["available"]
        )
    )

    price_changed = sorted(
        handle
        for handle in common
        if (
            get_state(
                previous[handle]
            )["price"]
            !=
            get_state(
                current[handle]
            )["price"]
        )
    )

    return {
        "added": added,
        "removed": removed,
        "stock_changed": stock_changed,
        "price_changed": price_changed
    }


# ============================================================
# CSV
# ============================================================

def save_products_csv(
    path,
    products
):

    path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    with path.open(
        "w",
        encoding="utf-8-sig",
        newline=""
    ) as f:

        fields = [
            "handle",
            "title",
            "price",
            "available",
            "tags",
            "url",
            "image"
        ]

        writer = csv.DictWriter(
            f,
            fieldnames=fields
        )

        writer.writeheader()

        for product in (
            products.values()
        ):

            images = (
                product.get(
                    "images"
                )
                or [""]
            )

            writer.writerow({
                "handle":
                    product.get(
                        "handle",
                        ""
                    ),

                "title":
                    product.get(
                        "title",
                        ""
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
                        ""
                    ),

                "image":
                    images[0]
            })


def save_history_csv(
    path,
    history
):

    path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    with path.open(
        "w",
        encoding="utf-8-sig",
        newline=""
    ) as f:

        fields = [
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
            "url"
        ]

        writer = csv.DictWriter(
            f,
            fieldnames=fields
        )

        writer.writeheader()

        for record in (
            history.values()
        ):

            first = (
                record.get(
                    "first"
                )
                or {}
            )

            last = (
                record.get(
                    "last"
                )
                or {}
            )

            writer.writerow({
                "handle":
                    record.get(
                        "handle",
                        ""
                    ),

                "title":
                    record.get(
                        "title",
                        ""
                    ),

                "visible":
                    record.get(
                        "visible",
                        False
                    ),

                "first_seen":
                    record.get(
                        "first_seen",
                        ""
                    ),

                "last_seen":
                    record.get(
                        "last_seen",
                        ""
                    ),

                "first_price":
                    first.get(
                        "price",
                        ""
                    ),

                "last_price":
                    last.get(
                        "price",
                        ""
                    ),

                "first_available":
                    first.get(
                        "available",
                        False
                    ),

                "last_available":
                    last.get(
                        "available",
                        False
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
                        ""
                    )
            })


# ============================================================
# Discord
# ============================================================

def send_discord(
    content
):

    if not DISCORD_WEBHOOK:

        print(
            "DISCORD_WEBHOOK is not configured."
        )

        return

    response = requests.post(
        DISCORD_WEBHOOK,
        json={
            "content": content
        },
        timeout=30
    )

    response.raise_for_status()


def send_error(
    monitor_name,
    message
):

    text = (
        "🚨 **FINDME STORE監視エラー**\n\n"
        f"対象: {monitor_name}\n"
        f"{message}"
    )

    print(
        text,
        file=sys.stderr
    )

    try:
        send_discord(
            text
        )
    except Exception as e:
        print(
            f"Discord notification failed: {e}",
            file=sys.stderr
        )


def product_line(
    product
):

    status = (
        "販売中"
        if is_available(product)
        else "Sold out"
    )

    price = (
        get_price_text(product)
        or "不明"
    )

    return (
        f"・{product.get('title', '(no title)')}"
        f" | {price}円"
        f" | {status}"
    )


def build_notification(
    monitor,
    previous,
    current,
    diff
):

    name = monitor[
        "name"
    ]

    sections = []

    # 新商品
    if diff["added"]:

        lines = [
            f"🆕 **{name} / 新商品**"
        ]

        for handle in diff["added"]:

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
                    ""
                )
            )

        sections.append(
            "\n".join(lines)
        )

    # 非表示
    if diff["removed"]:

        lines = [
            f"🗑️ **{name} / 非表示**"
        ]

        for handle in diff["removed"]:

            product = previous[
                handle
            ]

            lines.append(
                f"・{product.get('title', handle)}"
            )

            lines.append(
                product.get(
                    "url",
                    ""
                )
            )

        sections.append(
            "\n".join(lines)
        )

    # 在庫
    if diff["stock_changed"]:

        lines = [
            f"📦 **{name} / 在庫変更**"
        ]

        for handle in diff["stock_changed"]:

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
                    ""
                )
            )

        sections.append(
            "\n".join(lines)
        )

    # 価格
    if diff["price_changed"]:

        lines = [
            f"💰 **{name} / 価格変更**"
        ]

        for handle in diff["price_changed"]:

            old = previous[
                handle
            ]

            new = current[
                handle
            ]

            lines.append(
                f"・{new.get('title', handle)}"
            )

            lines.append(
                f"  {get_price_text(old) or '不明'}円"
                f" → "
                f"{get_price_text(new) or '不明'}円"
            )

            lines.append(
                new.get(
                    "url",
                    ""
                )
            )

        sections.append(
            "\n".join(lines)
        )

    if not sections:
        return None

    return (
        "🛒 **FINDME STORE監視**\n\n"
        +
        "\n\n".join(
            sections
        )
    )


# ============================================================
# Pages
# ============================================================

def save_pages_data(
    monitor,
    timestamp,
    products,
    history
):

    collection = monitor[
        "collection"
    ]

    pages_dir = (
        DOCS_DATA_DIR
        /
        collection
    )

    pages_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    products_data = {
        "collection":
            collection,

        "name":
            monitor["name"],

        "updated_at":
            timestamp,

        "product_count":
            len(products),

        "products":
            products
    }

    history_data = {
        "collection":
            collection,

        "name":
            monitor["name"],

        "updated_at":
            timestamp,

        "history_count":
            len(history),

        "history":
            history
    }

    atomic_write_json(
        pages_dir / "products.json",
        products_data
    )

    atomic_write_json(
        pages_dir / "history.json",
        history_data
    )


# ============================================================
# Monitor one collection
# ============================================================

def monitor_collection(
    monitor
):

    collection = monitor[
        "collection"
    ]

    name = monitor[
        "name"
    ]

    timestamp = now_iso()

    data_dir = (
        DATA_DIR
        /
        collection
    )

    data_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    products_json = (
        data_dir
        /
        "products.json"
    )

    products_csv = (
        data_dir
        /
        "products.csv"
    )

    history_json = (
        data_dir
        /
        "history.json"
    )

    history_csv = (
        data_dir
        /
        "history.csv"
    )

    print()
    print("=" * 70)
    print(
        f"Monitoring: {name}"
    )
    print(
        f"Collection: {collection}"
    )
    print(
        f"Pages: {monitor['pages']}"
    )
    print("=" * 70)

    # --------------------------------------------------------
    # Fetch all pages
    # --------------------------------------------------------

    raw_products = []

    for page in monitor[
        "pages"
    ]:

        products = fetch_page(
            monitor,
            page
        )

        raw_products.extend(
            products
        )

    current = build_products(
        raw_products
    )

    current_count = len(
        current
    )

    if current_count == 0:
        raise RuntimeError(
            f"[{name}] "
            "Current product count is zero."
        )

    # --------------------------------------------------------
    # Load previous
    # --------------------------------------------------------

    previous = load_json(
        products_json,
        {}
    )

    if not isinstance(
        previous,
        dict
    ):
        raise RuntimeError(
            f"[{name}] "
            "products.json must be object."
        )

    previous_count = len(
        previous
    )

    print(
        f"[{name}] "
        f"previous={previous_count}, "
        f"current={current_count}"
    )

    # --------------------------------------------------------
    # First run
    # --------------------------------------------------------

    if previous_count == 0:

        history = {}

        for handle, product in (
            current.items()
        ):

            history[
                handle
            ] = create_history(
                product,
                timestamp
            )

        atomic_write_json(
            products_json,
            current
        )

        atomic_write_json(
            history_json,
            history
        )

        save_products_csv(
            products_csv,
            current
        )

        save_history_csv(
            history_csv,
            history
        )

        save_pages_data(
            monitor,
            timestamp,
            current,
            history
        )

        send_discord(
            "📌 **監視開始**\n\n"
            f"対象: {name}\n"
            f"商品数: {current_count}\n"
            "初回取得のため差分なし"
        )

        return

    # --------------------------------------------------------
    # Safety: sudden decrease
    # --------------------------------------------------------

    minimum_count = max(
        1,
        int(
            previous_count
            *
            MIN_COUNT_RATIO
        )
    )

    if current_count < minimum_count:

        raise RuntimeError(
            f"[{name}] "
            "商品数が異常に減少しました。\n"
            f"前回: {previous_count}\n"
            f"今回: {current_count}\n"
            f"最低許容値: {minimum_count}\n"
            "安全のためデータ更新を中止しました。"
        )

    # --------------------------------------------------------
    # Compare
    # --------------------------------------------------------

    diff = compare(
        previous,
        current
    )

    # --------------------------------------------------------
    # History
    # --------------------------------------------------------

    history = load_json(
        history_json,
        {}
    )

    if not isinstance(
        history,
        dict
    ):
        raise RuntimeError(
            f"[{name}] "
            "history.json must be object."
        )

    # 現在存在する商品
    for handle, product in (
        current.items()
    ):

        if handle in history:

            history[
                handle
            ] = update_history(
                history[handle],
                product,
                timestamp
            )

        else:

            history[
                handle
            ] = create_history(
                product,
                timestamp
            )

    # 非表示になった商品
    for handle in diff[
        "removed"
    ]:

        if handle in history:

            history[
                handle
            ]["visible"] = False

    # --------------------------------------------------------
    # Save current
    # --------------------------------------------------------

    atomic_write_json(
        products_json,
        current
    )

    atomic_write_json(
        history_json,
        history
    )

    save_products_csv(
        products_csv,
        current
    )

    save_history_csv(
        history_csv,
        history
    )

    # --------------------------------------------------------
    # Save GitHub Pages data
    # --------------------------------------------------------

    save_pages_data(
        monitor,
        timestamp,
        current,
        history
    )

    # --------------------------------------------------------
    # Discord
    # --------------------------------------------------------

    notification = build_notification(
        monitor,
        previous,
        current,
        diff
    )

    if notification:

        send_discord(
            notification
        )


# ============================================================
# Main
# ============================================================

def main():

    monitors = load_monitors()

    success = 0
    failed = 0

    for monitor in monitors:

        try:

            monitor_collection(
                monitor
            )

            success += 1

        except Exception as e:

            failed += 1

            send_error(
                monitor.get(
                    "name",
                    monitor.get(
                        "collection",
                        "unknown"
                    )
                ),
                str(e)
            )

    print()
    print("=" * 70)
    print(
        f"Success: {success}"
    )
    print(
        f"Failed: {failed}"
    )
    print("=" * 70)

    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()

