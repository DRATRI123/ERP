import assert from "node:assert/strict";
import { test } from "node:test";

import { canSee, canSeePresence, type BusinessAccess, type ErpEvent } from "../src/permissions.js";
import { toEvent } from "../src/stream.js";

const access = (over: Partial<BusinessAccess> = {}): BusinessAccess => ({
  business_id: "b1", business_name: "B", role: "billing_operator", all_branches: true, branch_ids: [],
  permissions: ["invoices.view", "stock.view"], ...over });
const ev = (type: string, data: Record<string, unknown> = {}, business_id = "b1"): ErpEvent => ({
  id: "e", type, business_id, created_at: "", data });

test("permission per event family", () => {
  assert.equal(canSee(access(), ev("invoice.created")), true);
  assert.equal(canSee(access(), ev("stock.low")), true);
  assert.equal(canSee(access(), ev("payment.created")), false);
  assert.equal(canSee(access(), ev("journal.created")), false);
});

test("documents follow their side", () => {
  assert.equal(canSee(access(), ev("document.finalized", { doc_type: "sales_invoice" })), true);
  assert.equal(canSee(access(), ev("document.finalized", { doc_type: "purchase_invoice" })), false);
});

test("deny by default and isolate businesses", () => {
  assert.equal(canSee(access(), ev("webhook.test")), false);
  assert.equal(canSee(access(), ev("something.new")), false);
  assert.equal(canSee(access(), ev("invoice.created", {}, "b2")), false);
});

test("branch scoping", () => {
  const scoped = access({ all_branches: false, branch_ids: ["br1"] });
  assert.equal(canSee(scoped, ev("invoice.created", { branch_id: "br1" })), true);
  assert.equal(canSee(scoped, ev("invoice.created", { branch_id: "br2" })), false);
  assert.equal(canSee(scoped, ev("stock.low", {})), true); // business-wide event
});

test("presence needs users.view", () => {
  assert.equal(canSeePresence(access()), false);
  assert.equal(canSeePresence(access({ permissions: ["users.view"] })), true);
});

test("stream entries decode", () => {
  const e = toEvent(["id", "x", "type", "invoice.created", "business_id", "b1", "created_at", "t",
                     "data", JSON.stringify({ number: "INV/1" })]);
  assert.equal(e.type, "invoice.created");
  assert.deepEqual(e.data, { number: "INV/1" });
});
