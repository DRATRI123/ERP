"""Printed labels in English, Hindi and Gujarati (or bilingual, English first)."""

from __future__ import annotations

import enum


class LabelLanguage(enum.StrEnum):
    EN = "en"
    HI = "hi"
    GU = "gu"
    EN_HI = "en_hi"  # "Bill to / बिल प्राप्तकर्ता"
    EN_GU = "en_gu"


# English -> (Hindi, Gujarati). Tax names (CGST, SGST, IGST, Cess, HSN/SAC, GSTIN, IRN) stay as they are.
LABELS: dict[str, tuple[str, str]] = {
    "Tax Invoice": ("कर बीजक", "ટેક્સ ઇન્વૉઇસ"),
    "Bill of Supply": ("आपूर्ति बिल", "બિલ ઑફ સપ્લાય"),
    "Export Invoice": ("निर्यात बीजक", "નિકાસ ઇન્વૉઇસ"),
    "Credit Note": ("क्रेडिट नोट", "ક્રેડિટ નોટ"),
    "Debit Note": ("डेबिट नोट", "ડેબિટ નોટ"),
    "Quotation": ("कोटेशन", "ભાવપત્રક"),
    "Estimate": ("अनुमान", "અંદાજપત્રક"),
    "Proforma Invoice": ("प्रोफ़ॉर्मा बीजक", "પ્રોફોર્મા ઇન્વૉઇસ"),
    "Sales Order": ("बिक्री आदेश", "વેચાણ ઑર્ડર"),
    "Delivery Challan": ("डिलीवरी चालान", "ડિલિવરી ચલણ"),
    "Purchase Order": ("क्रय आदेश", "ખરીદી ઑર્ડર"),
    "Purchase Invoice": ("क्रय बिल", "ખરીદી બિલ"),
    "Purchase Return": ("क्रय वापसी", "ખરીદી પરત"),
    "No": ("क्रमांक", "નંબર"),
    "Date": ("दिनांक", "તારીખ"),
    "Due": ("देय तिथि", "ચુકવણી તારીખ"),
    "Place of supply": ("आपूर्ति का स्थान", "પુરવઠાનું સ્થળ"),
    "Tax payable on reverse charge": ("रिवर्स चार्ज पर देय कर", "રિવર્સ ચાર્જ પર ભરવાપાત્ર કર"),
    "Supplier inv.": ("आपूर्तिकर्ता बिल", "સપ્લાયર બિલ"),
    "Doctor": ("डॉक्टर", "ડૉક્ટર"),
    "Patient": ("रोगी", "દર્દી"),
    "Rx No": ("पर्ची क्रमांक", "પ્રિસ્ક્રિપ્શન નંબર"),
    "CANCELLED": ("रद्द", "રદ"),
    "Bill to": ("बिल प्राप्तकर्ता", "બિલ મેળવનાર"),
    "Ship to": ("माल प्राप्तकर्ता", "માલ મેળવનાર"),
    "Cash Sale": ("नकद बिक्री", "રોકડ વેચાણ"),
    "Item": ("वस्तु", "વસ્તુ"),
    "Batch": ("बैच", "બેચ"),
    "Exp.": ("समाप्ति", "મુદત"),
    "Qty": ("मात्रा", "જથ્થો"),
    "Unit": ("इकाई", "એકમ"),
    "MRP": ("एमआरपी", "એમઆરપી"),
    "Rate": ("दर", "દર"),
    "Disc.": ("छूट", "વળતર"),
    "Taxable": ("कर योग्य", "કરપાત્ર"),
    "GST %": ("जीएसटी %", "જીએસટી %"),
    "Tax": ("कर", "કર"),
    "Amount": ("राशि", "રકમ"),
    "Taxable value": ("कर योग्य मूल्य", "કરપાત્ર કિંમત"),
    "Discount included": ("शामिल छूट", "સમાવેલ વળતર"),
    "Round off": ("पूर्णांकन", "રાઉન્ડ ઑફ"),
    "Total": ("कुल", "કુલ"),
    "TOTAL": ("कुल", "કુલ"),
    "Paid": ("भुगतान किया", "ચૂકવેલ"),
    "Balance due": ("शेष देय", "બાકી રકમ"),
    "Balance": ("शेष", "બાકી"),
    "Amount in words": ("शब्दों में राशि", "શબ્દોમાં રકમ"),
    "Bank details": ("बैंक विवरण", "બેંક વિગતો"),
    "Terms": ("शर्तें", "શરતો"),
    "Notes": ("टिप्पणी", "નોંધ"),
    "E-way bill": ("ई-वे बिल", "ઈ-વે બિલ"),
    "Scan to pay": ("भुगतान हेतु स्कैन करें", "ચુકવણી માટે સ્કૅન કરો"),
    "Authorised Signatory": ("अधिकृत हस्ताक्षरकर्ता", "અધિકૃત સહીકર્તા"),
    "Original for Recipient": ("प्राप्तकर्ता हेतु मूल प्रति", "ગ્રાહક માટે મૂળ નકલ"),
    "Duplicate for Transporter": ("परिवहनकर्ता हेतु द्वितीय प्रति", "પરિવહનકર્તા માટે બીજી નકલ"),
    "Triplicate for Supplier": ("आपूर्तिकर्ता हेतु तृतीय प्रति", "સપ્લાયર માટે ત્રીજી નકલ"),
    "for": ("कृते", "વતી"),
}


def translator(lang: LabelLanguage | str):  # type: ignore[no-untyped-def]
    """A function label -> printed text for the chosen language; unknown labels stay English."""
    lang = LabelLanguage(lang)

    def t(label: str) -> str:
        pair = LABELS.get(label)
        if pair is None or lang == LabelLanguage.EN:
            return label
        hi, gu = pair
        local = hi if lang in (LabelLanguage.HI, LabelLanguage.EN_HI) else gu
        return f"{label} / {local}" if lang in (LabelLanguage.EN_HI, LabelLanguage.EN_GU) else local

    return t
