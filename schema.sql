CREATE TABLE IF NOT EXISTS manual_products (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    sku TEXT,
    price INTEGER NOT NULL DEFAULT 0,
    available INTEGER NOT NULL DEFAULT 1,
    category TEXT,
    tags TEXT NOT NULL DEFAULT '[]',
    source TEXT NOT NULL DEFAULT 'manual',
    source_name TEXT,
    url TEXT,
    image_url TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_manual_products_sku
ON manual_products(sku);

CREATE INDEX IF NOT EXISTS idx_manual_products_created_at
ON manual_products(created_at);

CREATE INDEX IF NOT EXISTS idx_manual_products_updated_at
ON manual_products(updated_at);

CREATE INDEX IF NOT EXISTS idx_manual_products_category
ON manual_products(category);