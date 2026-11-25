"use client";

/**
 * Offline billing: a local copy of the product list for search and scanning, and a queue of
 * bills made without internet that are sent when the connection returns.
 *
 * Each queued bill carries a ``client_ref``. Creating a document with a client_ref the server has
 * already seen returns that same document, so a bill is never created twice even if the
 * connection drops halfway through a sync. Totals are worked out by the server when the bill
 * syncs; a "paid in full" bill is paid for exactly that total.
 */

import { useSyncExternalStore } from "react";
import { create } from "zustand";

import { api, ApiError } from "@/lib/api";
import type { Me } from "@/stores/auth";
import type { Product } from "@/lib/types";

const PRODUCTS = (biz: string) => `erp-offline-products:${biz}`;
const QUEUE = (biz: string) => `erp-offline-queue:${biz}`;
const ME = "erp-offline-me";
const MAX_PAGES = 25; // 25 x 200 = 5,000 products

export interface QueuedBill {
  client_ref: string;
  created_at: string;
  label: string; // e.g. "Walk-in · 3 items"
  estimate: string; // approximate total shown when it was saved
  body: Record<string, unknown>; // POST /documents body
  pay_mode: string | null; // paid in full by this mode, or credit (null)
  error?: string;
  attempts: number;
}

function read<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

function write(key: string, value: unknown): boolean {
  try {
    localStorage.setItem(key, JSON.stringify(value));
    return true;
  } catch {
    return false; // storage full or blocked
  }
}

export const isOnline = () => typeof navigator === "undefined" || navigator.onLine !== false;

/** A fetch that never reached the server (no internet, DNS, server down). */
export function isNetworkError(e: unknown): boolean {
  return !isOnline() || e instanceof TypeError || (e instanceof ApiError && (e.status === 0 || e.status === 502 || e.status === 504));
}

// ---------------------------------------------------------------------------- signed-in user

export function rememberMe(me: Me) {
  write(ME, me);
}

export function rememberedMe(): Me | null {
  return read<Me | null>(ME, null);
}

export function forgetOfflineData() {
  try {
    for (const k of Object.keys(localStorage)) if (k.startsWith("erp-offline-")) localStorage.removeItem(k);
  } catch {
    /* nothing stored */
  }
}

// ---------------------------------------------------------------------------- products

export async function cacheProducts(base: string, biz: string): Promise<number> {
  const all: Product[] = [];
  for (let page = 0; page < MAX_PAGES; page++) {
    const res = await api<{ items: Product[]; total: number }>(`${base}/products`, { query: { limit: 200, offset: page * 200, with_stock: true } });
    all.push(...res.items);
    if (all.length >= res.total || res.items.length < 200) break;
  }
  // Keep only what billing needs, so thousands of products fit in the browser's storage.
  const slim = all.map((p) => ({
    id: p.id, sku: p.sku, name: p.name, kind: p.kind, hsn_sac: p.hsn_sac, barcode: p.barcode, unit_id: p.unit_id,
    selling_price: p.selling_price, purchase_price: null, mrp: p.mrp, gst_rate: p.gst_rate, prices_include_tax: p.prices_include_tax,
    track_inventory: p.track_inventory, batch_tracking: p.batch_tracking, serial_tracking: p.serial_tracking, is_active: p.is_active,
    reorder_level: null, category_id: p.category_id, on_hand: p.on_hand, composition: p.composition, rack: p.rack,
    drug_schedule: p.drug_schedule, pack_size: p.pack_size, pack_label: p.pack_label,
  }));
  write(PRODUCTS(biz), { at: new Date().toISOString(), items: slim });
  return slim.length;
}

export function cachedProducts(biz: string): { at: string | null; items: Product[] } {
  return read(PRODUCTS(biz), { at: null, items: [] as Product[] });
}

export function searchCached(biz: string, term: string, limit = 8): Product[] {
  const t = term.toLowerCase();
  const items = cachedProducts(biz).items.filter((p) => p.is_active !== false);
  const exact = items.filter((p) => p.barcode === term || p.sku.toLowerCase() === t);
  const rest = items.filter((p) => !exact.includes(p) && (p.name.toLowerCase().includes(t) || p.sku.toLowerCase().includes(t)
    || (typeof p.composition === "string" && p.composition.toLowerCase().includes(t)) || p.hsn_sac === term));
  return [...exact, ...rest].slice(0, limit);
}

export function lookupCached(biz: string, code: string): Product | undefined {
  return cachedProducts(biz).items.find((p) => p.barcode === code || p.sku.toLowerCase() === code.toLowerCase());
}

// ---------------------------------------------------------------------------- queue

interface OfflineState { pending: number; failed: number; syncing: boolean; lastSync: string | null; refresh: (biz: string) => void }
export const useOffline = create<OfflineState>((set) => ({
  pending: 0, failed: 0, syncing: false, lastSync: null,
  refresh: (biz) => {
    const q = queuedBills(biz);
    set({ pending: q.length, failed: q.filter((b) => b.error).length });
  },
}));

export function queuedBills(biz: string): QueuedBill[] {
  return read<QueuedBill[]>(QUEUE(biz), []);
}

export function enqueueBill(biz: string, bill: Omit<QueuedBill, "created_at" | "attempts">): boolean {
  const q = queuedBills(biz);
  if (q.some((b) => b.client_ref === bill.client_ref)) return true;
  const ok = write(QUEUE(biz), [...q, { ...bill, created_at: new Date().toISOString(), attempts: 0 }]);
  useOffline.getState().refresh(biz);
  return ok;
}

export function discardBill(biz: string, clientRef: string) {
  write(QUEUE(biz), queuedBills(biz).filter((b) => b.client_ref !== clientRef));
  useOffline.getState().refresh(biz);
}

/** Send queued bills. Stops at the first network failure; a refused bill keeps its error for the user. */
export async function syncQueue(base: string, biz: string): Promise<{ sent: number; failed: number }> {
  const st = useOffline.getState();
  if (st.syncing || !isOnline()) return { sent: 0, failed: 0 };
  useOffline.setState({ syncing: true });
  let sent = 0, failed = 0;
  try {
    for (const bill of queuedBills(biz)) {
      try {
        const doc = await api<{ id: string; status: string; amount_payable: string; balance_due: string }>(`${base}/documents`, {
          body: { ...bill.body, client_ref: bill.client_ref } });
        if (doc.status === "draft") {
          const payments = bill.pay_mode && Number(doc.amount_payable) > 0 ? [{ mode: bill.pay_mode, amount: doc.amount_payable }] : [];
          try {
            await api(`${base}/documents/${doc.id}/finalize`, { body: { payments } });
          } catch (e) {
            if (!(e instanceof ApiError && e.status === 409 && /already finalized/i.test(e.message))) throw e;
          }
        }
        discardBill(biz, bill.client_ref);
        sent++;
      } catch (e) {
        if (isNetworkError(e)) break;
        failed++;
        const q = queuedBills(biz).map((b) => (b.client_ref === bill.client_ref
          ? { ...b, attempts: b.attempts + 1, error: e instanceof ApiError ? e.message : String(e) } : b));
        write(QUEUE(biz), q);
      }
    }
  } finally {
    useOffline.setState({ syncing: false, lastSync: new Date().toISOString() });
    useOffline.getState().refresh(biz);
  }
  return { sent, failed };
}

/** Rough total for a bill made offline (the server's total is final): price × qty − discount, plus GST. */
export function estimateTotal(lines: { quantity: string; unit_price: string; discount_percent: string; gst_rate?: string; incl?: boolean }[]): number {
  let total = 0;
  for (const l of lines) {
    const gross = Number(l.quantity || 0) * Number(l.unit_price || 0) * (1 - Number(l.discount_percent || 0) / 100);
    const r = Number(l.gst_rate || 0) / 100;
    total += l.incl ? gross : gross * (1 + r);
  }
  return Math.round(total);
}

// ---------------------------------------------------------------------------- online status


function subscribe(cb: () => void) {
  window.addEventListener("online", cb);
  window.addEventListener("offline", cb);
  return () => { window.removeEventListener("online", cb); window.removeEventListener("offline", cb); };
}

/** Live browser online/offline state (true on the server). */
export function useOnline(): boolean {
  return useSyncExternalStore(subscribe, () => navigator.onLine, () => true);
}
