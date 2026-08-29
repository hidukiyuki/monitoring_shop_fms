import csv
import json
import os
import sys
from pathlib import Path

import requests


BASE_URL = "https://findmestore.thinkr.jp/collections/isekaijoucho/products.json"

PAGES = [1, 2]

STORE_BASE = "https://findmestore.thinkr.jp"

DATA_DIR = Path("data")
JSON_PATH = DATA_DIR / "products.json"
CSV_PATH = DATA_DIR / "products.csv"

# 前回の商品数に対して、これ以下まで急減したら異常扱い
MIN_COUNT_RATIO = 0.5

TIMEOUT = 30

DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK", "")


def fetch_page(page):
    url = BASE_URL

    print(f"Fetching page={page}")

    response = requests.get(
        url,
        params={
            "limit": 250,
            "page": page,
        },
        timeout=TIMEOUT,
        headers={
            # "User-Agent": "Mozilla/5.0 (compatible; ShopMonitor/1.0)"
        },
    )

    response.raise_for_status()

    data = response.json()

    if not isinstance(data, dict):
        raise RuntimeError(
            f"page={page}: response is not an object"
        )

    products = data.get("products")

    if not isinstance(products, list):
        raise RuntimeError(
            f"page={page}: products is not a list"
        )

    print(f"page={page}: {len(products)} products")

    return products


def normalize_product(product):
    handle = product.get("handle")

    if not handle:
        return None

    variants = product.get("variants") or []

    normalized_variants = []

    for variant in variants:
        normalized_variants.append({
            "id": variant.get("id"),
            "title": variant.get("title"),
            "price": variant.get("price"),
            "available": variant.get("available"),
        })

    images = []

    for image in product.get("images") or []:
        src = image.get("src")

        if src:
            images.append(src)

    return {
        "id": product.get("id"),
        "handle": handle,
        "title": product.get("title"),
        "vendor": product.get("vendor"),
        "product_type": product.get("product_type"),
        "tags": sorted(product.get("tags") or []),
        "url": f"{STORE_BASE}/products/{handle}",
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

        # 同じhandleが複数回出ても1商品にまとめる
        products[handle] = normalized

    return dict(sorted(products.items()))


def load_previous():
    if not JSON_PATH.exists():
        return {}

    try:
        with JSON_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, dict):
            raise RuntimeError("previous JSON is not an object")

        return data

    except Exception as e:
        raise RuntimeError(
            f"failed to read previous JSON: {e}"
        )


def save_json(products):
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    with JSON_PATH.open("w", encoding="utf-8") as f:
        json.dump(
            products,
            f,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )


def variant_summary(product):
    variants = product.get("variants", [])

    if not variants:
        return {
            "price": "",
            "available": False,
        }

    prices = []
    available = False

    for variant in variants:
        price = variant.get("price")

        if price is not None:
            prices.append(str(price))

        if variant.get("available") is True:
            available = True

    price = ""

    if prices:
        # 同一価格なら1つだけ
        unique_prices = sorted(set(prices))

        if len(unique_prices) == 1:
            price = unique_prices[0]
        else:
            price = " / ".join(unique_prices)

    return {
        "price": price,
        "available": available,
    }


def product_row(product):
    summary = variant_summary(product)

    return {
        "handle": product.get("handle", ""),
        "title": product.get("title", ""),
        "price": summary["price"],
        "available": summary["available"],
        "tags": ", ".join(product.get("tags", [])),
        "url": product.get("url", ""),
        "image": (
            product.get("images", [""])[0]
            if product.get("images")
            else ""
        ),
    }


def save_csv(products):
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    rows = [
        product_row(product)
        for product in products.values()
    ]

    fieldnames = [
        "handle",
        "title",
        "price",
        "available",
        "tags",
        "url",
        "image",
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

def save_html(products):
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    rows = []

    for product in products.values():
        summary = variant_summary(product)

        title = product.get("title", "")
        handle = product.get("handle", "")
        url = product.get("url", "")
        image = (
            product.get("images", [""])[0]
            if product.get("images")
            else ""
        )
        tags = ", ".join(product.get("tags", []))
        price_value = summary["price"]
        available = summary["available"]

        status_text = "販売中" if available else "Sold out"

        image_html = ""

        if image:
            image_html = f'''
                <img
                    src="{image}"
                    alt="{title}"
                    loading="lazy"
                >
            '''

        rows.append(f"""
        <tr>
            <td class="image">
                {image_html}
            </td>
            <td>
                <a href="{url}" target="_blank">
                    {title}
                </a>
                <div class="handle">{handle}</div>
            </td>
            <td>
                {price_value or "不明"} 円
            </td>
            <td>
                <span class="status {'available' if available else 'soldout'}">
                    {status_text}
                </span>
            </td>
            <td>
                {tags}
            </td>
        </tr>
        """)

    html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">

<meta name="viewport"
      content="width=device-width, initial-scale=1.0">

<title>FINDME STORE / ヰ世界情緒</title>

<style>

body {{
    font-family:
        -apple-system,
        BlinkMacSystemFont,
        "Segoe UI",
        sans-serif;

    margin: 0;
    padding: 24px;

    background: #f5f5f5;
    color: #222;
}}

.container {{
    max-width: 1400px;
    margin: auto;
}}

h1 {{
    margin-bottom: 8px;
}}

.info {{
    color: #666;
    margin-bottom: 20px;
}}

table {{
    width: 100%;
    border-collapse: collapse;
    background: white;
}}

th,
td {{
    padding: 12px;
    border-bottom: 1px solid #ddd;
    text-align: left;
    vertical-align: middle;
}}

th {{
    background: #eee;
    position: sticky;
    top: 0;
}}

.image {{
    width: 120px;
}}

.image img {{
    width: 100px;
    height: 100px;
    object-fit: contain;
}}

.handle {{
    color: #888;
    font-size: 12px;
    margin-top: 5px;
}}

.status {{
    display: inline-block;
    padding: 4px 8px;
    border-radius: 4px;
    font-size: 13px;
}}

.available {{
    background: #dff5df;
    color: #176b17;
}}

.soldout {{
    background: #eee;
    color: #777;
}}

@media (max-width: 800px) {{

    body {{
        padding: 10px;
    }}

    table {{
        font-size: 13px;
    }}

    th,
    td {{
        padding: 7px;
    }}

    .image {{
        width: 70px;
    }}

    .image img {{
        width: 60px;
        height: 60px;
    }}

}}

</style>

</head>

<body>

<div class="container">

<h1>FINDME STORE / ヰ世界情緒</h1>

<div class="info">
商品数: {len(products)}
<br>
最終更新: {__import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
</div>

<table>

<thead>
<tr>
    <th>画像</th>
    <th>商品</th>
    <th>価格</th>
    <th>状態</th>
    <th>タグ</th>
</tr>
</thead>

<tbody>

{"".join(rows)}

</tbody>

</table>

</div>

</body>
</html>
"""

    with (DATA_DIR / "products.html").open(
        "w",
        encoding="utf-8",
    ) as f:
        f.write(html)

def status(product):
    return (
        "販売中"
        if variant_summary(product)["available"]
        else "Sold out"
    )


def price(product):
    return variant_summary(product)["price"]


def compare(previous, current):
    previous_keys = set(previous)
    current_keys = set(current)

    added = sorted(current_keys - previous_keys)
    removed = sorted(previous_keys - current_keys)

    changed = []
    stock_changed = []
    price_changed = []

    for handle in sorted(previous_keys & current_keys):
        old = previous[handle]
        new = current[handle]

        old_summary = variant_summary(old)
        new_summary = variant_summary(new)

        if old_summary["available"] != new_summary["available"]:
            stock_changed.append(handle)

        if old_summary["price"] != new_summary["price"]:
            price_changed.append(handle)

        # 商品情報そのものの変更
        if old != new:
            changed.append(handle)

    return {
        "added": added,
        "removed": removed,
        "changed": changed,
        "stock_changed": stock_changed,
        "price_changed": price_changed,
    }


def product_line(product):
    return (
        f"・{product.get('title', '(no title)')} "
        f"| {price(product) or '価格不明'}円 "
        f"| {status(product)}"
    )


def send_discord(content):
    if not DISCORD_WEBHOOK:
        print("DISCORD_WEBHOOK is not configured")
        return

    response = requests.post(
        DISCORD_WEBHOOK,
        json={
            "content": content[:1900],
        },
        timeout=30,
    )

    response.raise_for_status()


def send_error(message):
    print(f"ERROR: {message}", file=sys.stderr)

    if DISCORD_WEBHOOK:
        try:
            send_discord(
                "🚨 **FINDME STORE監視エラー**\n\n"
                f"{message}"
            )
        except Exception as e:
            print(
                f"Discord error notification failed: {e}",
                file=sys.stderr,
            )


def build_notification(previous, current, diff):
    messages = []

    added = diff["added"]
    removed = diff["removed"]
    stock_changed = diff["stock_changed"]
    price_changed = diff["price_changed"]

    # 新規
    if added:
        messages.append("🆕 **新商品**")

        for handle in added:
            messages.append(
                product_line(current[handle])
            )

    # 削除
    if removed:
        messages.append("\n🗑️ **商品削除**")

        for handle in removed:
            product = previous[handle]

            messages.append(
                f"・{product.get('title', handle)} "
                f"| {product.get('url', '')}"
            )

    # 在庫変更
    if stock_changed:
        messages.append("\n📦 **在庫変更**")

        for handle in stock_changed:
            old = previous[handle]
            new = current[handle]

            messages.append(
                f"・{new.get('title', handle)}\n"
                f"  {status(old)} → {status(new)}\n"
                f"  {new.get('url', '')}"
            )

    # 価格変更
    if price_changed:
        messages.append("\n💰 **価格変更**")

        for handle in price_changed:
            old = previous[handle]
            new = current[handle]

            messages.append(
                f"・{new.get('title', handle)}\n"
                f"  {price(old) or '不明'} → "
                f"{price(new) or '不明'}\n"
                f"  {new.get('url', '')}"
            )

    if not messages:
        return None

    messages.insert(
        0,
        "🛒 **FINDME STORE / ヰ世界情緒**",
    )

    return "\n".join(messages)


def main():
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)

        # 全ページ取得
        raw_products = []

        for page in PAGES:
            page_products = fetch_page(page)

            # 空レスポンス防止
            if len(page_products) == 0:
                raise RuntimeError(
                    f"page={page}: empty products response"
                )

            raw_products.extend(page_products)

        # 正規化
        current = build_products(raw_products)

        current_count = len(current)

        print(f"Total unique products: {current_count}")

        if current_count == 0:
            raise RuntimeError(
                "normalized product count is zero"
            )

        previous = load_previous()

        previous_count = len(previous)

        print(
            f"Previous products: {previous_count}"
        )

        # 初回実行
        if previous_count == 0:
            print(
                "No previous data. "
                "Creating initial snapshot."
            )

            save_json(current)
            save_csv(current)
            save_html(current)

            send_discord(
                "📌 **FINDME STORE監視を開始しました**\n\n"
                f"取得商品数: {current_count}\n"
                "今回は初回取得のため、差分通知はありません。"
            )

            return

        # 件数激減防止
        minimum_count = int(
            previous_count * MIN_COUNT_RATIO
        )

        if current_count < minimum_count:
            raise RuntimeError(
                "商品数が異常に減少しました。\n"
                f"前回: {previous_count}\n"
                f"今回: {current_count}\n"
                f"許容最低値: {minimum_count}\n"
                "安全のため前回データを更新しません。"
            )

        diff = compare(
            previous,
            current,
        )

        print(
            f"Added: {len(diff['added'])}"
        )
        print(
            f"Removed: {len(diff['removed'])}"
        )
        print(
            f"Stock changed: "
            f"{len(diff['stock_changed'])}"
        )
        print(
            f"Price changed: "
            f"{len(diff['price_changed'])}"
        )

        # 現在データを保存
        save_json(current)
        save_csv(current)

        # Discord
        notification = build_notification(
            previous,
            current,
            diff,
        )

        if notification:
            send_discord(notification)
        else:
            print("No changes.")

    except Exception as e:
        send_error(str(e))
        sys.exit(1)


if __name__ == "__main__":
    main()