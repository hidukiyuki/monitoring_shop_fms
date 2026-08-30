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

PAGES_DATA_DIR = Path("docs/data")


# ============================================================
# Settings
# ============================================================

STORE_BASE = "https://findmestore.thinkr.jp"

TIMEOUT = 30

# 前回の商品数に対する最低許容割合
# 50%未満になった場合は異常と判断して更新しない
MIN_COUNT_RATIO = 0.5

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

    temp_path.replace(
        path
    )


def load_json(path, default):
    if not path.exists():
        return default

    if path.stat().st_size == 0:
        raise RuntimeError(
            f"JSON file is empty: {path}"
        )

    try:
        with path.open(
            "r",
            encoding="utf-8",
        ) as f:

            return json.load(f)

    except json.JSONDecodeError as e:

        raise RuntimeError(
            f"Invalid JSON: {path}: {e}"
        )


# ============================================================
# Monitor config
# ============================================================

def load_monitors():

    config = load_json(
        CONFIG_FILE,
        {},
    )

    if not isinstance(
        config,
        dict,
    ):

        raise RuntimeError(
            "monitors.json must be an object."
        )

    monitors = config.get(
        "monitors"
    )

    if not isinstance(
        monitors,
        list,
    ):

        raise RuntimeError(
            'monitors.json must contain "monitors" array.'
        )

    if not monitors:

        raise RuntimeError(
            "No monitors configured."
        )

    collections = set()

    for monitor in monitors:

        if not isinstance(
            monitor,
            dict,
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
                "Monitor is missing collection."
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
                f"{collection}: missing name."
            )

        if not url:

            raise RuntimeError(
                f"{collection}: missing url."
            )

        if not isinstance(
            pages,
            list,
        ) or not pages:

            raise RuntimeError(
                f"{collection}: pages must be a non-empty list."
            )

        for page in pages:

            if (
                not isinstance(
                    page,
                    int,
                )
                or page < 1
            ):

                raise RuntimeError(
                    f"{collection}: page must be integer >= 1."
                )

    return monitors


# ============================================================
# Shopify JSON API
# ============================================================

def fetch_page(monitor, page):

    name = monitor["name"]

    print(
        f"[{name}] Fetching page={page}"
    )

    response = requests.get(
        monitor["url"],
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
        dict,
    ):

        raise RuntimeError(
            f"[{name}] page={page}: response is not object"
        )

    products = data.get(
        "products"
    )

    if not isinstance(
        products,
        list,
    ):

        raise RuntimeError(
            f"[{name}] page={page}: products is not list"
        )

    if not products:

        raise RuntimeError(
            f"[{name}] page={page}: empty products list"
        )

    print(
        f"[{name}] page={page}: "
        f"{len(products)} products"
    )

    return products


# ============================================================
# Normalize
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
                "id": variant.get("id"),
                "title": variant.get("title"),
                "price": variant.get("price"),
                "available": variant.get("available"),
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

            images.append(
                src
            )

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

        "id":
            product.get("id"),

        "handle":
            handle,

        "title":
            product.get("title"),

        "vendor":
            product.get("vendor"),

        "product_type":
            product.get("product_type"),

        "tags":
            tags,

        "url":
            f"{STORE_BASE}/products/{handle}",

        "images":
            images,

        "variants":
            variants,
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
# State
# ============================================================

def get_price(product):

    prices = []

    for variant in (
        product.get(
            "variants"
        )
        or []
    ):

        try:

            prices.append(
                float(
                    variant.get(
                        "price"
                    )
                )
            )

        except (
            TypeError,
            ValueError,
        ):

            pass

    if not prices:
        return None

    return min(
        prices
    )


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

        "price":
            get_price_text(
                product
            ),

        "available":
            is_available(
                product
            ),

        "tags":
            list(
                product.get(
                    "tags"
                )
                or []
            ),

        "images":
            list(
                product.get(
                    "images"
                )
                or []
            ),
    }


# ============================================================
# History
# ============================================================

def create_history_record(
    product,
    timestamp,
):

    state = get_state(
        product
    )

    return {

        "handle":
            product.get(
                "handle"
            ),

        "title":
            product.get(
                "title"
            ),

        "url":
            product.get(
                "url"
            ),

        "first_seen":
            timestamp,

        "last_seen":
            timestamp,

        "visible":
            True,

        "first":
            state,

        "last":
            state,
    }


def update_history_record(
    record,
    product,
    timestamp,
):

    record["title"] = (
        product.get(
            "title"
        )
    )

    record["url"] = (
        product.get(
            "url"
        )
    )

    record["last_seen"] = (
        timestamp
    )

    record["visible"] = True

    record["last"] = (
        get_state(
            product
        )
    )

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

    common = (
        previous_keys
        &
        current_keys
    )

    return {

        "added":
            sorted(
                current_keys
                -
                previous_keys
            ),

        "removed":
            sorted(
                previous_keys
                -
                current_keys
            ),

        "stock_changed":
            sorted(
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
            ),

        "price_changed":
            sorted(
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
            ),
    }


# ============================================================
# CSV
# ============================================================

def save_products_csv(
    path,
    products,
):

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with path.open(
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

        for product in (
            products.values()
        ):

            writer.writerow({

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
            })


def save_history_csv(
    path,
    history,
):

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with path.open(
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
            })


# ============================================================
# Discord
# ============================================================

def send_discord(content):

    if not DISCORD_WEBHOOK:

        print(
            "DISCORD_WEBHOOK is not configured."
        )

        return

    response = requests.post(
        DISCORD_WEBHOOK,
        json={
            "content":
                content
        },
        timeout=30,
    )

    response.raise_for_status()


def send_error(
    name,
    message,
):

    content = (
        "🚨 **FINDME STORE監視エラー**\n\n"
        f"対象: {name}\n"
        f"{message}"
    )

    print(
        content,
        file=sys.stderr,
    )

    try:

        send_discord(
            content
        )

    except Exception as e:

        print(
            f"Discord error: {e}",
            file=sys.stderr,
        )


def product_line(product):

    available = (
        "販売中"
        if is_available(
            product
        )
        else "Sold out"
    )

    price = (
        get_price_text(
            product
        )
        or "不明"
    )

    return (
        f"・{product.get('title', '(no title)')}"
        f" | {price}円"
        f" | {available}"
    )


def build_notification(
    monitor,
    previous,
    current,
    diff,
):

    name = monitor[
        "name"
    ]

    sections = []

    # --------------------------------------------------------
    # Added
    # --------------------------------------------------------

    if diff["added"]:

        lines = [
            f"🆕 **{name} / 新商品**"
        ]

        for handle in (
            diff["added"]
        ):

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
            "\n".join(
                lines
            )
        )

    # --------------------------------------------------------
    # Removed
    # --------------------------------------------------------

    if diff["removed"]:

        lines = [
            f"🗑️ **{name} / 非表示**"
        ]

        for handle in (
            diff["removed"]
        ):

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
            "\n".join(
                lines
            )
        )

    # --------------------------------------------------------
    # Stock
    # --------------------------------------------------------

    if diff["stock_changed"]:

        lines = [
            f"📦 **{name} / 在庫変更**"
        ]

        for handle in (
            diff["stock_changed"]
        ):

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
            "\n".join(
                lines
            )
        )

    # --------------------------------------------------------
    # Price
    # --------------------------------------------------------

    if diff["price_changed"]:

        lines = [
            f"💰 **{name} / 価格変更**"
        ]

        for handle in (
            diff["price_changed"]
        ):

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
                    "",
                )
            )

        sections.append(
            "\n".join(
                lines
            )
        )

    if not sections:
        return None

    return (
        "🛒 **FINDME STORE監視**\n\n"
        + "\n\n".join(
            sections
        )
    )


# ============================================================
# GitHub Pages
# ============================================================

def write_pages(
    monitor,
    timestamp,
    current,
    history,
    products_path,
    history_path,
):

    products_data = {

        "collection":
            monitor["collection"],

        "name":
            monitor["name"],

        "updated_at":
            timestamp,

        "product_count":
            len(current),

        "products":
            current,
    }

    history_data = {

        "collection":
            monitor["collection"],

        "name":
            monitor["name"],

        "updated_at":
            timestamp,

        "history_count":
            len(history),

        "history":
            history,
    }

    atomic_write_json(
        products_path,
        products_data,
    )

    atomic_write_json(
        history_path,
        history_data,
    )


# ============================================================
# Collection
# ============================================================

def monitor_collection(
    monitor,
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

    pages_dir = (
        PAGES_DATA_DIR
        /
        collection
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

    pages_products_json = (
        pages_dir
        /
        "products.json"
    )

    pages_history_json = (
        pages_dir
        /
        "history.json"
    )

    print()
    print("=" * 60)
    print(f"Monitoring: {name}")
    print(f"Collection: {collection}")
    print(f"Pages: {monitor['pages']}")
    print("=" * 60)

    # --------------------------------------------------------
    # Fetch
    # --------------------------------------------------------

    raw_products = []

    for page in monitor["pages"]:

        raw_products.extend(
            fetch_page(
                monitor,
                page,
            )
        )

    current = build_products(
        raw_products
    )

    current_count = len(
        current
    )

    if current_count == 0:

        raise RuntimeError(
            f"[{name}] Current product count is zero."
        )

    # --------------------------------------------------------
    # Previous
    # --------------------------------------------------------

    previous = load_json(
        products_json,
        {},
    )

    if not isinstance(
        previous,
        dict,
    ):

        raise RuntimeError(
            f"[{name}] "
            "products.json must be an object."
        )

    previous_count = len(
        previous
    )

    print(
        f"[{name}] "
        f"{previous_count} → "
        f"{current_count}"
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
            ] = create_history_record(
                product,
                timestamp,
            )

        atomic_write_json(
            products_json,
            current,
        )

        atomic_write_json(
            history_json,
            history,
        )

        save_products_csv(
            products_csv,
            current,
        )

        save_history_csv(
            history_csv,
            history,
        )

        write_pages(
            monitor,
            timestamp,
            current,
            history,
            pages_products_json,
            pages_history_json,
        )

        send_discord(
            "📌 **監視開始**\n\n"
            f"対象: {name}\n"
            f"商品数: {current_count}\n"
            "初回取得のため差分なし"
        )

        return

    # --------------------------------------------------------
    # Safety
    # --------------------------------------------------------

    minimum_count = max(
        1,
        int(
            previous_count
            *
            MIN_COUNT_RATIO
        ),
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
    # Difference
    # --------------------------------------------------------

    diff = compare_products(
        previous,
        current,
    )

    # --------------------------------------------------------
    # History
    # --------------------------------------------------------

    history = load_json(
        history_json,
        {},
    )

    if not isinstance(
        history,
        dict,
    ):

        raise RuntimeError(
            f"[{name}] "
            "history.json must be an object."
        )

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

    # 非表示商品は履歴を残す
    for handle in diff["removed"]:

        if handle in history:

            history[
                handle
            ]["visible"] = False

    # --------------------------------------------------------
    # Save
    # --------------------------------------------------------

    atomic_write_json(
        products_json,
        current,
    )

    atomic_write_json(
        history_json,
        history,
    )

    save_products_csv(
        products_csv,
        current,
    )

    save_history_csv(
        history_csv,
        history,
    )

    write_pages(
        monitor,
        timestamp,
        current,
        history,
        pages_products_json,
        pages_history_json,
    )

    # --------------------------------------------------------
    # Discord
    # --------------------------------------------------------

    notification = (
        build_notification(
            monitor,
            previous,
            current,
            diff,
        )
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
                        "unknown",
                    ),
                ),
                str(e),
            )

    print()
    print("=" * 60)
    print(f"Success: {success}")
    print(f"Failed: {failed}")
    print("=" * 60)

    if failed:

        sys.exit(1)


if __name__ == "__main__":

    main()