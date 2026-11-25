"use client";

import { useCallback } from "react";

import { useWorkspace } from "@/stores/business";

export type Lang = "en" | "hi" | "gu";
export const LANGS: [Lang, string][] = [["en", "English"], ["hi", "हिन्दी"], ["gu", "ગુજરાતી"]];

/** English text -> [Hindi, Gujarati]. Covers the menus and the billing counter; other text stays English. */
const T: Record<string, [string, string]> = {
  // menu
  "Dashboard": ["डैशबोर्ड", "ડેશબોર્ડ"], "All businesses": ["सभी व्यवसाय", "બધા વ્યવસાય"],
  "Sales & Invoices": ["बिक्री और बिल", "વેચાણ અને બિલ"], "Purchases": ["खरीद", "ખરીદી"],
  "Products & Inventory": ["उत्पाद और स्टॉक", "ઉત્પાદનો અને સ્ટોક"], "Damage & claims": ["टूट-फूट और दावे", "નુકસાન અને દાવા"],
  "Manufacturing": ["उत्पादन", "ઉત્પાદન"], "Pricing & Schemes": ["मूल्य और योजनाएँ", "ભાવ અને સ્કીમ"],
  "WhatsApp catalogue": ["व्हाट्सऐप कैटलॉग", "વૉટ્સએપ કૅટલૉગ"], "Field sales": ["फ़ील्ड बिक्री", "ફીલ્ડ વેચાણ"],
  "Customers": ["ग्राहक", "ગ્રાહકો"], "Suppliers": ["आपूर्तिकर्ता", "સપ્લાયર"], "Loyalty points": ["लॉयल्टी पॉइंट", "લૉયલ્ટી પૉઇન્ટ"],
  "Doctors": ["डॉक्टर", "ડૉક્ટર"], "Accounting": ["लेखा", "હિસાબ"], "Banking & Payments": ["बैंक और भुगतान", "બેંક અને ચુકવણી"],
  "Cheques (PDC)": ["चेक (पीडीसी)", "ચેક (પીડીસી)"], "TDS": ["टीडीएस", "ટીડીએસ"], "Interest on dues": ["बकाया पर ब्याज", "બાકી પર વ્યાજ"],
  "Expense claims": ["खर्च दावे", "ખર્ચ દાવા"], "Staff & Salary": ["कर्मचारी और वेतन", "સ્ટાફ અને પગાર"],
  "GST & Tax": ["जीएसटी और कर", "જીએસટી અને કર"], "Reports": ["रिपोर्ट", "રિપોર્ટ"], "Documents": ["दस्तावेज़", "દસ્તાવેજો"],
  "Licences": ["लाइसेंस", "લાઇસન્સ"], "Automation": ["ऑटोमेशन", "ઑટોમેશન"], "Integrations": ["इंटीग्रेशन", "ઇન્ટિગ્રેશન"],
  "Developer API": ["डेवलपर API", "ડેવલપર API"], "Users & Permissions": ["उपयोगकर्ता और अनुमतियाँ", "વપરાશકર્તા અને પરવાનગી"],
  "Businesses": ["व्यवसाय", "વ્યવસાય"], "Settings": ["सेटिंग्स", "સેટિંગ્સ"], "Help & guide": ["सहायता", "મદદ"],
  "Operations": ["संचालन", "કામકાજ"], "Finance": ["वित्त", "નાણાં"], "Workspace": ["कार्यक्षेत्र", "કાર્યક્ષેત્ર"],
  // tab bar / top bar / account
  "Home": ["होम", "હોમ"], "Sales": ["बिक्री", "વેચાણ"], "Stock": ["स्टॉक", "સ્ટોક"], "More": ["और", "વધુ"], "New bill": ["नया बिल", "નવું બિલ"],
  "Search…": ["खोजें…", "શોધો…"], "or jump to": ["या सीधे जाएँ", "અથવા સીધા જાઓ"], "New invoice": ["नया बिल", "નવું બિલ"],
  "Language": ["भाषा", "ભાષા"], "Sign out": ["लॉग आउट", "લૉગ આઉટ"], "Keyboard shortcuts": ["कीबोर्ड शॉर्टकट", "કીબોર્ડ શૉર્ટકટ"],
  "Security & 2FA": ["सुरक्षा और 2FA", "સુરક્ષા અને 2FA"], "Collapse": ["छोटा करें", "નાનું કરો"],
  // billing counter
  "New sale": ["नई बिक्री", "નવું વેચાણ"], "New purchase": ["नई खरीद", "નવી ખરીદી"], "Edit draft": ["ड्राफ्ट बदलें", "ડ્રાફ્ટ બદલો"],
  "Document": ["दस्तावेज़", "દસ્તાવેજ"], "Customer": ["ग्राहक", "ગ્રાહક"], "Supplier": ["आपूर्तिकर्ता", "સપ્લાયર"], "Date": ["दिनांक", "તારીખ"],
  "Walk-in name (optional)": ["ग्राहक का नाम (वैकल्पिक)", "ગ્રાહકનું નામ (વૈકલ્પિક)"], "Walk-in customer": ["नकद ग्राहक", "રોકડ ગ્રાહક"],
  "Choose supplier": ["आपूर्तिकर्ता चुनें", "સપ્લાયર પસંદ કરો"], "Due date": ["देय तिथि", "ચુકવણી તારીખ"], "Supplier bill no.": ["आपूर्तिकर्ता बिल नं.", "સપ્લાયર બિલ નં."],
  "Rate": ["दर", "દર"], "Item": ["वस्तु", "વસ્તુ"], "Qty": ["मात्रा", "જથ્થો"], "Disc %": ["छूट %", "વળતર %"], "Amount": ["राशि", "રકમ"],
  "Summary": ["सारांश", "સારાંશ"], "Subtotal": ["उप-योग", "પેટા સરવાળો"], "Discount": ["छूट", "વળતર"], "Taxable value": ["कर योग्य मूल्य", "કરપાત્ર કિંમત"],
  "Round off": ["पूर्णांकन", "રાઉન્ડ ઑફ"], "Total": ["कुल", "કુલ"], "Payment received": ["प्राप्त भुगतान", "મળેલી ચુકવણી"],
  "Payment made": ["किया गया भुगतान", "કરેલી ચુકવણી"], "Balance due": ["शेष देय", "બાકી રકમ"], "Save & finalize": ["सेव और पक्का करें", "સેવ અને પાકું કરો"],
  "Save & issue": ["सेव और जारी करें", "સેવ અને જારી કરો"], "Save draft": ["ड्राफ्ट सेव करें", "ડ્રાફ્ટ સેવ કરો"],
  "Scan barcode or search product (F2)": ["बारकोड स्कैन करें या उत्पाद खोजें (F2)", "બારકોડ સ્કૅન કરો અથવા ઉત્પાદન શોધો (F2)"],
  "Add items to start": ["शुरू करने के लिए वस्तुएँ जोड़ें", "શરૂ કરવા વસ્તુઓ ઉમેરો"], "Notes (printed)": ["टिप्पणी (छपेगी)", "નોંધ (છપાશે)"],
  "Add service / custom line": ["सेवा / अन्य पंक्ति जोड़ें", "સેવા / અન્ય લાઇન ઉમેરો"], "Cash": ["नकद", "રોકડ"], "Card": ["कार्ड", "કાર્ડ"],
  "Bank": ["बैंक", "બેંક"], "Cheque": ["चेक", "ચેક"], "Clear": ["हटाएँ", "સાફ કરો"], "Split payment": ["भुगतान बाँटें", "ચુકવણી વહેંચો"],
  "Retail": ["खुदरा", "છૂટક"], "Wholesale": ["थोक", "જથ્થાબંધ"],
  "Quotation": ["कोटेशन", "ભાવપત્રક"], "Estimate": ["अनुमान", "અંદાજપત્રક"], "Proforma invoice": ["प्रोफ़ॉर्मा बिल", "પ્રોફોર્મા બિલ"],
  "Sales order": ["बिक्री आदेश", "વેચાણ ઑર્ડર"], "Delivery challan": ["डिलीवरी चालान", "ડિલિવરી ચલણ"], "Sales invoice": ["बिक्री बिल", "વેચાણ બિલ"],
  "Credit note": ["क्रेडिट नोट", "ક્રેડિટ નોટ"], "Debit note": ["डेबिट नोट", "ડેબિટ નોટ"], "Purchase order": ["क्रय आदेश", "ખરીદી ઑર્ડર"],
  "Purchase bill": ["खरीद बिल", "ખરીદી બિલ"], "Purchase return": ["खरीद वापसी", "ખરીદી પરત"],
};

export function translate(lang: Lang, text: string): string {
  if (lang === "en") return text;
  const pair = T[text];
  return pair ? pair[lang === "hi" ? 0 : 1] : text;
}

/** t("Total") in the language chosen in the account menu (English when there is no translation). */
export function useT() {
  const lang = useWorkspace((s) => s.lang ?? "en");
  return useCallback((text: string) => translate(lang, text), [lang]);
}
