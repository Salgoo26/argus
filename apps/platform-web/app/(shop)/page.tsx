"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { api, errorMessage, type Product, won } from "@/lib/api";

export default function ShopHome() {
  const [products, setProducts] = useState<Product[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<{ items: Product[] }>("/shop/products")
      .then((body) => setProducts(body.items))
      .catch((e) => setError(errorMessage(e)));
  }, []);

  return (
    <>
      <section className="card hero">
        <h1 className="page-title">데모 커머스</h1>
      </section>
      {error && <div className="alert-error">{error}</div>}
      <div className="grid">
        {products.map((p) => (
          <div key={p.id} className="card product">
            <div className="product-thumb" aria-hidden>
              {p.name.slice(0, 1)}
            </div>
            <div className="product-name">{p.name}</div>
            <div className="product-price">{won(p.price)}</div>
            <Link href={`/checkout/${p.id}`} className="btn btn-primary">
              구매하기
            </Link>
          </div>
        ))}
      </div>
    </>
  );
}
