"""Document library: content checks, de-duplication, versions, trash, and who sees linked files."""

from __future__ import annotations

import pytest

from app.core.config import get_settings
from tests.conftest import USER_PASSWORD, Shop
from tests.test_billing import customer, doc, finalize, invoice, stocked

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def up(s: Shop, name: str, data: bytes, expect: int = 201, headers=None, **form):
    r = s.api.c.post(f"{s.base}/files", headers=headers or s.h, files={"file": (name, data, "application/octet-stream")},
                     data={k: str(v) for k, v in form.items()})
    assert r.status_code == expect, r.text
    return r.json()


@pytest.fixture
def fshop(api, owner_headers) -> Shop:
    return Shop(api, owner_headers)


def test_upload_list_download_and_folders(fshop):
    f = up(fshop, "GST certificate.pdf", PDF, folder=" Licences / 2026 ", tags="gst, Licence,gst", description="Registration")
    assert f["folder"] == "/Licences/2026" and f["content_type"] == "application/pdf" and f["tags"] == ["gst", "licence"]
    up(fshop, "shop.png", PNG, folder="/Licences")
    up(fshop, "price list.csv", b"sku,price\nASH,295\n", folder="/Price lists")
    page = fshop.get("/files", folder="/Licences", recursive="true")
    assert {x["name"] for x in page["items"]} == {"GST certificate.pdf", "shop.png"}
    assert fshop.get("/files", folder="/Licences")["total"] == 1
    assert fshop.get("/files", tag="GST")["total"] == 1 and fshop.get("/files", tag="gs")["total"] == 0
    assert fshop.get("/files", q="registration")["items"][0]["id"] == f["id"]
    folders = fshop.get("/files/folders")
    assert [x["folder"] for x in folders["folders"]] == ["/Licences", "/Licences/2026", "/Price lists"]
    assert folders["usage"]["blobs"] == 3 and folders["max_upload_mb"] == get_settings().max_upload_mb

    r = fshop.api.c.get(f"{fshop.base}/files/{f['id']}/download", headers=fshop.h)
    assert r.status_code == 200 and r.content == PDF
    assert r.headers["content-type"] == "application/pdf" and r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["content-disposition"].startswith('attachment; filename="GST certificate.pdf"')
    assert "sandbox" in r.headers["content-security-policy"]


@pytest.mark.parametrize("name,data,code", [
    ("page.html", b"<!DOCTYPE html><html><script>alert(1)</script></html>", "file_type_not_allowed"),
    ("logo.svg", b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"><script>x</script></svg>', "file_type_not_allowed"),
    ("invoice.pdf", b"MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00\xff\xff\x00\x00" * 4, "file_type_not_allowed"),  # exe renamed
    ("empty.txt", b"", "empty_file"),
])
def test_dangerous_or_empty_files_are_refused(fshop, name, data, code):
    r = up(fshop, name, data, expect=422)
    assert r["error"]["code"] == code


def test_size_limit_and_names(fshop, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_upload_mb", 1)
    r = up(fshop, "big.txt", b"a" * (1024 * 1024 + 1), expect=422)
    assert r["error"]["code"] == "file_too_large"
    f = up(fshop, "../../etc/pass<wd>.txt", b"hello")
    assert f["name"] == "passwd.txt"
    assert up(fshop, "x.txt", b"hi", folder="/a/../b", expect=422)["error"]["code"] == "invalid_folder"


def test_versions_dedup_rename_trash_and_purge(fshop):
    f = up(fshop, "rent agreement.pdf", PDF)
    v2 = PDF.replace(b"1.4", b"1.7")
    r = fshop.api.c.post(f"{fshop.base}/files/{f['id']}/versions", headers=fshop.h, files={"file": ("rent agreement v2.pdf", v2)},
                         data={"note": "Renewed for 2027"})
    assert r.status_code == 201 and r.json()["version"] == 2
    detail = fshop.get(f"/files/{f['id']}")
    assert detail["current_version"] == 2 and [v["version"] for v in detail["versions"]] == [2, 1]
    assert fshop.api.c.get(f"{fshop.base}/files/{f['id']}/download", headers=fshop.h).content == v2
    old = fshop.api.c.get(f"{fshop.base}/files/{f['id']}/download", headers=fshop.h, params={"version": 1})
    assert old.content == PDF and "v1 - rent agreement.pdf" in old.headers["content-disposition"]

    copy = up(fshop, "same bytes.pdf", PDF)  # identical content is stored once
    assert fshop.get("/files/folders")["usage"]["blobs"] == 2

    r = fshop.api.c.patch(f"{fshop.base}/files/{f['id']}", headers=fshop.h, json={"name": "Rent 2027.pdf", "folder": "/Legal", "tags": ["rent"]})
    assert r.status_code == 200 and r.json()["folder"] == "/Legal"

    assert fshop.api.c.delete(f"{fshop.base}/files/{f['id']}", headers=fshop.h, params={"permanent": "true"}).status_code == 422
    assert fshop.api.c.delete(f"{fshop.base}/files/{f['id']}", headers=fshop.h).status_code == 204
    assert fshop.get("/files")["total"] == 1 and fshop.get("/files", trash="true")["total"] == 1
    assert fshop.post(f"/files/{f['id']}/restore", {}, expect=200)["deleted_at"] is None
    fshop.api.c.delete(f"{fshop.base}/files/{f['id']}", headers=fshop.h)
    assert fshop.api.c.delete(f"{fshop.base}/files/{f['id']}", headers=fshop.h, params={"permanent": "true"}).status_code == 204
    assert fshop.api.c.get(f"{fshop.base}/files/{f['id']}", headers=fshop.h).status_code == 404
    # v1's content is still used by the other file; v2's content went with the purge.
    assert fshop.get("/files/folders")["usage"]["blobs"] == 1
    assert fshop.api.c.get(f"{fshop.base}/files/{copy['id']}/download", headers=fshop.h).content == PDF


def test_linked_files_follow_the_records_permissions(api, owner_headers, fshop):
    p = stocked(fshop, "ashwa", selling_price="100")
    c = customer(fshop, "Clinic with prescription")
    inv = invoice(fshop, [{"product_id": p}], party_id=c["id"])
    bill = finalize(fshop, doc(fshop, "purchase_invoice", [{"product_id": p, "unit_price": "40"}],
                               party_id=fshop.post("/suppliers", {"name": "Herb Supplier"})["id"], supplier_invoice_no="HS/1")["id"])
    on_inv = up(fshop, "prescription.png", PNG, entity_type="document", entity_id=inv["id"])
    on_bill = up(fshop, "supplier bill.pdf", PDF, entity_type="document", entity_id=bill["id"])
    on_cust = up(fshop, "kyc.pdf", PDF, entity_type="party", entity_id=c["id"])
    free = up(fshop, "notes.txt", b"general notes")
    assert {x["id"] for x in fshop.get("/files", entity_type="document", entity_id=inv["id"])["items"]} == {on_inv["id"]}

    # A member who sees sales but not purchases or customers.
    role = fshop.post("/roles", {"name": "Sales files", "permissions": ["business.view", "files.view", "files.manage", "invoices.view"]})
    m = fshop.post("/members", {"email": "clerk@example.com", "full_name": "Clerk", "role_id": role["id"]})
    api.c.post("/api/v1/auth/accept-invite", json={"token": m["invite_token"], "password": USER_PASSWORD})
    h = api.auth("clerk@example.com", USER_PASSWORD)
    seen = {x["id"] for x in fshop.get("/files", headers=h)["items"]}
    assert seen == {on_inv["id"], free["id"]}
    for hidden in (on_bill, on_cust):
        assert api.c.get(f"{fshop.base}/files/{hidden['id']}/download", headers=h).status_code == 404
    # …and cannot attach files to records they cannot see.
    assert up(fshop, "x.pdf", PDF, headers=h, entity_type="party", entity_id=c["id"], expect=404)
    assert up(fshop, "x.pdf", PDF, headers=h, entity_type="document", entity_id=inv["id"])["entity_id"] == inv["id"]


def test_files_are_isolated_between_businesses(api, owner_headers, fshop):
    other = Shop(api, owner_headers, name="Other business")
    c = customer(other, "Other's customer")
    f = up(fshop, "mine.pdf", PDF)
    assert api.c.get(f"{other.base}/files/{f['id']}", headers=other.h).status_code == 404
    assert other.get("/files")["total"] == 0
    # Linking to another business's record is refused.
    assert up(fshop, "x.pdf", PDF, entity_type="party", entity_id=c["id"], expect=404)
