import { createClient } from "@libsql/client/web";

interface Env {
  TURSO_URL: string;
  TURSO_AUTH_TOKEN: string;

  ADMIN_TOKEN: string;

  ALLOWED_ORIGIN: string;
}

interface ProductInput {
  title: string;
  sku?: string;
  price?: number;
  available?: boolean;
  category?: string;
  tags?: string[];
  source_name?: string;
  url?: string;
  image_url?: string;
}

function getDB(env: Env) {
  return createClient({
    url: env.TURSO_URL,
    authToken: env.TURSO_AUTH_TOKEN,
  });
}

function headers(env: Env) {
  return {
    "Content-Type": "application/json; charset=utf-8",
    "Access-Control-Allow-Origin": env.ALLOWED_ORIGIN || "*",
    "Access-Control-Allow-Headers": "Content-Type, Authorization",
    "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
  };
}

function response(data: unknown, status: number, env: Env) {
  return new Response(JSON.stringify(data), {
    status,
    headers: headers(env),
  });
}

function fail(message: string, status: number, env: Env) {
  return response(
    {
      ok: false,
      error: message,
    },
    status,
    env,
  );
}

function authorized(request: Request, env: Env) {
  const value = request.headers.get("Authorization");

  return value === `Bearer ${env.ADMIN_TOKEN}`;
}

function text(value: unknown, maxLength = 1000): string {
  if (typeof value !== "string") {
    return "";
  }

  return value.trim().slice(0, maxLength);
}

function tags(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }

  return value
    .filter((value) => typeof value === "string")
    .map((value) => value.trim())
    .filter(Boolean)
    .slice(0, 100);
}

async function body(request: Request): Promise<ProductInput | null> {
  try {
    const value = await request.json();

    if (!value || typeof value !== "object") {
      return null;
    }

    return value as ProductInput;
  } catch {
    return null;
  }
}

function validate(product: ProductInput): string | null {
  if (!text(product.title, 300)) {
    return "商品名は必須です";
  }

  if (
    product.price !== undefined &&
    (!Number.isInteger(product.price) || product.price < 0)
  ) {
    return "価格が不正です";
  }

  return null;
}

function convertRow(row: any) {
  let parsedTags: string[] = [];

  try {
    parsedTags = JSON.parse(row.tags || "[]");
  } catch {
    parsedTags = [];
  }

  return {
    id: row.id,
    title: row.title,
    sku: row.sku || "",
    price: Number(row.price || 0),
    available: Boolean(row.available),
    category: row.category || "",
    tags: parsedTags,
    source: row.source || "manual",
    source_name: row.source_name || "",
    url: row.url || "",
    image_url: row.image_url || "",
    created_at: row.created_at,
    updated_at: row.updated_at,
  };
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);

    const path = url.pathname;

    if (request.method === "OPTIONS") {
      return new Response(null, {
        status: 204,
        headers: headers(env),
      });
    }

    /*
     * Health check
     */

    if (request.method === "GET" && path === "/health") {
      return response(
        {
          ok: true,
        },
        200,
        env,
      );
    }

    /*
     * GET /products
     *
     * 手動商品一覧
     *
     * 読み取りは公開。
     * 書き込みだけADMIN_TOKEN必須。
     */

    if (request.method === "GET" && path === "/products") {
      try {
        const db = getDB(env);

        const result = await db.execute(`
            SELECT
              id,
              title,
              sku,
              price,
              available,
              category,
              tags,
              source,
              source_name,
              url,
              image_url,
              created_at,
              updated_at
            FROM manual_products
            ORDER BY created_at DESC
          `);

        const products = result.rows.map(convertRow);

        return response(
          {
            ok: true,
            products,
          },
          200,
          env,
        );
      } catch (error) {
        console.error(error);

        return fail("DB error", 500, env);
      }
    }

    /*
     * POST /products
     *
     * 新規登録
     */

    if (request.method === "POST" && path === "/products") {
      if (!authorized(request, env)) {
        return fail("Unauthorized", 401, env);
      }

      const product = await body(request);

      if (!product) {
        return fail("JSONが不正です", 400, env);
      }

      const error = validate(product);

      if (error) {
        return fail(error, 400, env);
      }

      const id = `manual-${crypto.randomUUID()}`;

      const now = new Date().toISOString();

      try {
        const db = getDB(env);

        await db.execute({
          sql: `
            INSERT INTO manual_products (
              id,
              title,
              sku,
              price,
              available,
              category,
              tags,
              source,
              source_name,
              url,
              image_url,
              created_at,
              updated_at
            )
            VALUES (
              ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
          `,
          args: [
            id,

            text(product.title, 300),

            text(product.sku, 200),

            product.price ?? 0,

            product.available === false ? 0 : 1,

            text(product.category, 200),

            JSON.stringify(tags(product.tags)),

            "manual",

            text(product.source_name, 200),

            text(product.url, 2000),

            text(product.image_url, 2000),

            now,
            now,
          ],
        });

        return response(
          {
            ok: true,
            id,
          },
          201,
          env,
        );
      } catch (error) {
        console.error(error);

        return fail("DB error", 500, env);
      }
    }

    /*
     * PUT /products/:id
     *
     * 編集
     */

    if (request.method === "PUT" && path.startsWith("/products/")) {
      if (!authorized(request, env)) {
        return fail("Unauthorized", 401, env);
      }

      const id = decodeURIComponent(path.substring("/products/".length));

      if (!id) {
        return fail("IDがありません", 400, env);
      }

      const product = await body(request);

      if (!product) {
        return fail("JSONが不正です", 400, env);
      }

      const error = validate(product);

      if (error) {
        return fail(error, 400, env);
      }

      const now = new Date().toISOString();

      try {
        const db = getDB(env);

        const result = await db.execute({
          sql: `
              UPDATE manual_products
              SET
                title = ?,
                sku = ?,
                price = ?,
                available = ?,
                category = ?,
                tags = ?,
                source_name = ?,
                url = ?,
                image_url = ?,
                updated_at = ?
              WHERE id = ?
            `,
          args: [
            text(product.title, 300),

            text(product.sku, 200),

            product.price ?? 0,

            product.available === false ? 0 : 1,

            text(product.category, 200),

            JSON.stringify(tags(product.tags)),

            text(product.source_name, 200),

            text(product.url, 2000),

            text(product.image_url, 2000),

            now,
            id,
          ],
        });

        if (result.rowsAffected === 0) {
          return fail("商品がありません", 404, env);
        }

        return response(
          {
            ok: true,
          },
          200,
          env,
        );
      } catch (error) {
        console.error(error);

        return fail("DB error", 500, env);
      }
    }

    /*
     * DELETE /products/:id
     */

    if (request.method === "DELETE" && path.startsWith("/products/")) {
      if (!authorized(request, env)) {
        return fail("Unauthorized", 401, env);
      }

      const id = decodeURIComponent(path.substring("/products/".length));

      try {
        const db = getDB(env);

        const result = await db.execute({
          sql: `
              DELETE FROM manual_products
              WHERE id = ?
            `,
          args: [id],
        });

        if (result.rowsAffected === 0) {
          return fail("商品がありません", 404, env);
        }

        return response(
          {
            ok: true,
          },
          200,
          env,
        );
      } catch (error) {
        console.error(error);

        return fail("DB error", 500, env);
      }
    }

    return fail("Not Found", 404, env);
  },
};
