/**
 * In-app guides. Each topic belongs to one or more pages (``routes``) and is shown by the ⓘ
 * button in that page's header, and in the Help centre. Steps name the real buttons on screen,
 * so keep them in step with the UI when labels change.
 */

export interface HelpSection { heading: string; steps: string[]; note?: string }
export interface HelpTopic {
  id: string; title: string; group: string; routes: string[]; summary: string;
  sections: HelpSection[]; tips?: string[]; related?: string[];
}

export const HELP: HelpTopic[] = [
  // ------------------------------------------------------------------ start
  {
    id: "start", title: "Getting started", group: "Start here", routes: ["/dashboard", "/help"],
    summary: "The first things to set up, in order, so billing, stock and books are right from day one.",
    sections: [
      { heading: "Set up once (about 30 minutes)", steps: [
        "Settings → Business: legal name, GSTIN, address, phone, logo. These print on every invoice.",
        "Settings → Branches: one per shop or godown, with its own address and PIN code.",
        "Settings → Modules: pick your business type (pharmacy, manufacturer, distributor, retail, services) so only what you use shows in the menu.",
        "Settings → Numbering: invoice number series (e.g. INV/2026-27/0001). The defaults are fine to start.",
        "Licences → Add licence: drug licence, FSSAI, AYUSH… tick “Print number on invoices”.",
        "Products & Inventory → New product (or import a CSV/Excel file from Settings). For medicines switch on “Batches & expiry”.",
        "Opening stock: Products & Inventory → Movements tab → Opening stock, with quantity and cost.",
        "Customers / Suppliers → New: name, GSTIN, state, phone, credit limit and days.",
        "Users & Permissions → Invite member: give each person a role (billing operator, accountant…).",
        "Integrations: connect Gmail/SMTP and WhatsApp so invoices, reminders and reports can be sent.",
      ] },
      { heading: "Every day", steps: [
        "Bill at the counter: Sales & Invoices → New invoice (or Alt + N anywhere).",
        "Record supplier bills when goods arrive: Purchases → New purchase → Purchase bill.",
        "Record money received and paid: Banking & Payments → Record payment.",
        "Glance at the Dashboard: today’s sales, dues, low stock and expiring batches.",
      ] },
      { heading: "Every month", steps: [
        "GST & Tax: review GSTR-1 and GSTR-3B, then file on the GST portal.",
        "Staff & Salary → Salaries: calculate, post and mark paid.",
        "Reports: Profit & loss and Receivables aging. Schedule them by e-mail under Reports → Scheduled e-mails.",
      ] },
    ],
    tips: ["Press ? anywhere for keyboard shortcuts, and Ctrl + K to search anything.", "Every page has an ⓘ button at the top — it opens the guide for that page."],
    related: ["sales", "purchases", "inventory", "shortcuts"],
  },
  {
    id: "shortcuts", title: "Keyboard shortcuts", group: "Start here", routes: [],
    summary: "Work faster at the counter without the mouse.",
    sections: [
      { heading: "Anywhere", steps: [
        "Ctrl + K or / — search customers, products, documents and pages.",
        "Alt + N — new sales invoice.", "? — list of shortcuts.", "[ — collapse or expand the sidebar.",
        "G then a letter — go to a page: D dashboard, S sales, P purchases, I inventory, C customers, A accounting, T GST, R reports, U users, O all businesses.",
      ] },
      { heading: "On a bill", steps: [
        "F2 — jump to product search (scan a barcode or type a name/SKU and press Enter).",
        "F4 — choose the customer or supplier.", "F9 — save and finalize.", "Ctrl + S — save as draft.",
      ] },
    ],
  },
  {
    id: "offline", title: "Billing without internet", group: "Start here", routes: [],
    summary: "When the internet drops, keep billing. Bills are kept on the device and saved automatically when the connection returns.",
    sections: [
      { heading: "Prepare (once, while online)", steps: [
        "Open the app on the billing computer or phone and sign in. It keeps a copy of your product list (refreshed every 30 minutes).",
        "On a phone, “Add to Home screen” installs it like an app; it then opens even without internet.",
      ] },
      { heading: "When the internet is down", steps: [
        "A cloud icon “Offline” appears at the top. Search and barcode scanning use the saved product list.",
        "The total shown is approximate (marked). Take the payment and press Save & finalize: the bill is kept on this device.",
        "Walk-in sales must be paid in full; for credit, choose the customer.",
      ] },
      { heading: "When it is back", steps: [
        "Waiting bills are sent automatically within a minute — or press the cloud icon → Send now.",
        "Each bill gets its number and exact total from the server, and its payment is recorded for that total. A bill is never saved twice.",
        "If one is refused (e.g. out of stock of a batch), it stays under the cloud icon with the reason; fix it and bill again, then discard the queued one.",
      ] },
    ],
    tips: ["Bills made offline are kept in that browser only: send them before clearing the browser's data."],
  },
  {
    id: "language", title: "Hindi and Gujarati", group: "Start here", routes: [],
    summary: "Show the menus and billing screen in हिन्दी or ગુજરાતી, and print invoices with Hindi or Gujarati labels.",
    sections: [
      { heading: "Screens", steps: [
        "Account (your initials, top right) → Language → हिन्दी or ગુજરાતી. Each person chooses on their own device.",
        "The menu, phone tab bar, page titles and the whole billing screen change; less used pages stay in English.",
      ] },
      { heading: "Printed invoices", steps: [
        "Settings → Print templates → Printed labels: Hindi, Gujarati, or bilingual (English + Hindi / Gujarati).",
        "Labels like Bill to, Qty, Rate, Total, Balance due and the title (कर बीजक / ટેક્સ ઇન્વૉઇસ) print in that language. Product and customer names print as you typed them.",
      ] },
    ],
    tips: ["Type product names in Hindi or Gujarati if you like — search and printing work with them."],
  },
  {
    id: "dashboard", title: "Dashboard", group: "Start here", routes: ["/dashboard"],
    summary: "Today and this month at a glance for the selected business.",
    sections: [
      { heading: "What you see", steps: [
        "Sales today and this month, money to receive and to pay (with overdue), profit (if you may see it).",
        "Sales vs purchases chart for the last 30 days, recent documents and stock alerts.",
        "Switch business at the top of the sidebar; “All businesses” shows every business side by side (owner).",
      ] },
    ],
    related: ["owner", "reports"],
  },

  // ------------------------------------------------------------------ sales
  {
    id: "sales", title: "Sales: invoices, quotations, orders, challans, returns", group: "Selling",
    routes: ["/sales", "/sales/new"],
    summary: "Every sales document starts from Sales & Invoices → New invoice. Pick the kind of document in the “Document” box at the top left.",
    sections: [
      { heading: "Make a sales invoice (counter bill)", steps: [
        "Sales & Invoices → New invoice (or Alt + N).",
        "Document: Sales invoice (already selected).",
        "Customer (F4): pick one, add a new one, or leave “Walk-in customer” for cash sales (optionally type a name to print).",
        "Scan the barcode or type the product (F2) and press Enter. Change quantity, rate or discount % on the line.",
        "Batch: leave “Auto (first expiry)” — the system sells the earliest-expiring stock and splits the line if needed.",
        "Payment received: tap UPI / Cash / Card and adjust the amount. Walk-in sales must be paid in full; customers can owe.",
        "Save & finalize (F9). The invoice gets its number, stock and books update, and the print preview opens.",
      ], note: "Rates come from the customer’s price list if they have one. Type a rate to override it. Schemes like “10 + 1” add the free row automatically." },
      { heading: "Make a sales order (customer orders now, delivery/billing later)", steps: [
        "Sales & Invoices → New invoice → Document: Sales order.",
        "Choose the customer (F4), add the products and quantities.",
        "Save & issue. No stock moves and nothing is owed yet.",
        "When you deliver: open the order → ⋯ (More actions) → Create delivery challan, or Create sales invoice to bill straight away.",
        "Only quantities not yet delivered/billed are copied, so part deliveries work.",
      ], note: "Orders from the WhatsApp catalogue arrive as draft sales orders: open one, check prices, Finalize, then ⋯ → Create sales invoice." },
      { heading: "Quotation / estimate / proforma", steps: [
        "New invoice → Document: Quotation (or Estimate / Proforma invoice) → customer → items → Save & issue.",
        "Print or ⋯ → Send to customer.",
        "If accepted: open it → ⋯ → Create sales order or Create sales invoice.",
      ] },
      { heading: "Delivery challan (goods go before the bill)", steps: [
        "New invoice → Document: Delivery challan (or create it from a sales order) → Save & issue. Stock leaves now.",
        "Later: open the challan → ⋯ → Create sales invoice. Stock is not deducted twice.",
      ] },
      { heading: "Customer returns goods (credit note)", steps: [
        "Open the original invoice (Sales & Invoices → click it).",
        "⋯ → Create credit note. Reduce the quantities to what came back.",
        "Finalize. Stock comes back at its original cost and the customer’s balance reduces.",
      ] },
      { heading: "Charge extra after billing (debit note)", steps: ["Open the invoice → ⋯ → Create debit note → change amounts → Finalize."] },
      { heading: "Drafts, edits and cancelling", steps: [
        "Ctrl + S saves a draft; find drafts with Status: Draft on the Sales list. Open → Edit → Finalize.",
        "A final invoice cannot be edited (GST law). Fix it with a credit/debit note, or ⋯ → Cancel document with a reason.",
      ] },
      { heading: "Print, PDF, send, collect", steps: [
        "On any document: Print (A4 or thermal), PDF, or ⋯ → Send to customer (e-mail/WhatsApp/SMS once connected).",
        "Record payment on the invoice, or later in Banking & Payments.",
      ] },
    ],
    tips: ["Tabs at the top of Sales & Invoices filter by document kind; use Status and Payment filters to find unpaid invoices.", "Export this month gives the sales register as Excel."],
    related: ["payments", "customers", "pricing", "catalogue", "gst"],
  },
  {
    id: "document", title: "A document’s page", group: "Selling", routes: ["/documents/"],
    summary: "Everything about one invoice, order, bill or note: lines, tax, totals, payments and attachments.",
    sections: [
      { heading: "Buttons at the top", steps: [
        "Draft: Edit and Finalize. Final: Print, PDF and Record payment (when money is due).",
        "⋯ (More actions): Create … (the next document — order → invoice, invoice → credit note, PO → purchase bill, bill → purchase return), Send to customer, Cancel document.",
      ] },
      { heading: "On the right", steps: [
        "Totals with GST split, place of supply, e-invoice IRN and e-way bill when recorded.",
        "Attachments: drop the signed delivery note, prescription or supplier copy here.",
      ] },
    ],
    related: ["sales", "purchases", "documents-library"],
  },
  {
    id: "customers", title: "Customers and suppliers", group: "Selling", routes: ["/customers", "/suppliers"],
    summary: "People and businesses you sell to and buy from, with their balances and ledgers.",
    sections: [
      { heading: "Add one", steps: [
        "Customers (or Suppliers) → New → name, GSTIN (the state fills itself), phone, e-mail, address.",
        "Credit limit and credit days: billing warns or blocks above the limit; days set the due date.",
        "Customers only: Price list (hospital, doctor, wholesale prices) and Credit hold (only full-payment sales).",
      ] },
      { heading: "See their account", steps: [
        "Click a name: amount to receive/pay, overdue, open invoices, and the ledger for any period.",
        "Attachments at the bottom: KYC, agreements, licences of the buyer.",
      ] },
    ],
    related: ["pricing", "payments"],
  },
  {
    id: "pricing", title: "Price lists and schemes", group: "Selling", routes: ["/pricing"],
    summary: "Different prices for different kinds of customers, and trade schemes that apply on every bill automatically.",
    sections: [
      { heading: "Price list (e.g. Hospitals: MRP − 15 %)", steps: [
        "Pricing & Schemes → Price lists → New price list.",
        "Start from: Selling price, Wholesale, MRP or Purchase price; Adjust by % (−15 = 15 % less).",
        "Fixed prices: add products with their own price; add the same product again with a higher Min qty for slab prices.",
        "Give it to customers: Customers → edit → Price list.",
      ] },
      { heading: "Scheme (e.g. buy 10 get 1 free)", steps: [
        "Pricing & Schemes → Schemes → New scheme.",
        "Type: Free goods (buy X get Y) or Quantity discount %. Applies to one product or a whole category, with optional dates.",
        "On bills it applies itself: free goods appear as a green FREE row (zero value), discounts fill the Disc % box.",
      ] },
    ],
    related: ["sales", "customers"],
  },
  {
    id: "catalogue", title: "WhatsApp catalogue (online orders)", group: "Selling", routes: ["/catalogue", "/shop/"],
    summary: "One link your buyers open on their phone to order; orders arrive here as draft sales orders.",
    sections: [
      { heading: "Set it up", steps: [
        "WhatsApp catalogue → fill Title, your WhatsApp number, welcome text, categories to show, minimum order → Create catalogue.",
        "Share the link: Share (opens WhatsApp), Copy, or print the QR code for your counter.",
      ] },
      { heading: "When an order comes", steps: [
        "It appears under Online orders (and as a draft Sales order). The buyer’s WhatsApp opens with the order summary to your number.",
        "Open it → check prices/stock → Finalize → ⋯ → Create sales invoice.",
        "Get an e-mail for every order: Automation → template “New online order”.",
      ] },
      { heading: "Stop or change the link", steps: ["Switch off “Catalogue is open”, or Replace link — the old link and QR stop working at once."] },
    ],
    related: ["sales", "automation"],
  },

  // ------------------------------------------------------------------ buying
  {
    id: "pharmacy", title: "Pharmacy: doctors, prescriptions, salts, loose tablets", group: "Selling",
    routes: ["/doctors", "/sales/new", "/settings?tab=modules"],
    summary: "For a medical store: sell by strip or loose, with substitutes by salt, the prescription on Schedule H bills and the H1 register kept for you. Switch it on only for the business that needs it.",
    sections: [
      { heading: "Switch it on (only for your pharmacy business)", steps: [
        "Choose the pharmacy business in the business switcher (top left).",
        "Settings → Modules → turn on “Pharmacy / medical store”, or press the “Medical store / pharmacy” quick set-up.",
        "Your other businesses (manufacturing, distribution, retail…) never see these fields.",
      ] },
      { heading: "Set up each medicine (Products & Inventory → New product / Edit)", steps: [
        "Salt / composition: e.g. “Paracetamol 500 mg”. Searching the salt finds every brand, and the bill offers substitutes.",
        "Rack / shelf: e.g. “A-3”. Shown in search results and on the bill line, so staff find it fast.",
        "Drug schedule: H (prescription), H1 / X / Narcotic (prescription + register), G or None.",
        "PTR and PTS: price to retailer and to stockist, used when the bill’s Rate is PTR or PTS.",
        "Selling loose: keep the unit as one tablet and set Pack size 10, Pack name “strip”. Prices and stock are per tablet.",
      ], note: "Switch on “Batches & expiry” for medicines, so expiry is checked and the earliest batch is sold first." },
      { heading: "Billing a prescription", steps: [
        "Add the items. A Schedule H / H1 / X item shows a red badge and the Prescription box opens.",
        "Doctor: choose from the list, or press + to add a new doctor on the spot.",
        "Enter the patient’s name. For H1, X and narcotics also the Rx number (and the address for the register).",
        "Save & finalize stays disabled until these are filled. The doctor, patient and Rx print on the bill.",
        "For any other bill, “Add doctor / prescription” under the total records the doctor for doctor-wise sales.",
      ] },
      { heading: "Strips and loose tablets", steps: [
        "Quantity is in tablets. Under it you see e.g. “2 strip + 3 loose”.",
        "Press “+1 strip” to add a full strip, or type the number of tablets for loose sale.",
      ] },
      { heading: "Substitutes", steps: [
        "On a bill line, press “Substitutes” to see other brands with the same salt, with price, stock and rack.",
        "Press Use to swap the line to that brand; the quantity stays.",
      ] },
      { heading: "Near-expiry discount", steps: [
        "Settings → Modules (below the switches): set e.g. 60 days and 10%.",
        "When the batch being sold expires within 60 days, the line gets 10% off automatically, marked “Near expiry”.",
        "A line where you typed your own discount is left alone.",
      ] },
      { heading: "Registers and reports", steps: [
        "Reports → Pharmacy → Schedule H1 / X register: date, bill, patient, doctor with registration number, drug, batch and quantity. Export it as CSV or print it.",
        "Reports → Pharmacy → Doctor-wise sales: bills and value per doctor for any period. The Doctors page shows this month’s.",
      ] },
    ],
    tips: ["Keep the H1 register for 3 years: export it every month and save it in Documents.",
      "PTR / PTS only apply when the bill’s Rate is set to them; a customer’s price list still comes first."],
    related: ["sales", "expiry", "inventory", "reports"],
  },
  {
    id: "purchases", title: "Purchases: purchase orders, purchase bills, returns", group: "Buying",
    routes: ["/purchases", "/purchases/new"],
    summary: "Purchase order = what you ask the supplier for (no stock change). Purchase bill = goods and supplier’s bill received (stock and dues increase). Purchase return = goods sent back.",
    sections: [
      { heading: "Make a purchase order (PO)", steps: [
        "Purchases → New purchase → Document: Purchase order.",
        "Supplier (F4) → add products, quantities and agreed rates.",
        "Save & issue → Print or PDF and send it to the supplier. No stock or money changes yet.",
      ], note: "Quick way: Products & Inventory → Reorder → tick products → Create purchase orders (one per supplier)." },
      { heading: "Record a purchase bill (goods have arrived)", steps: [
        "From a PO: open the purchase order → ⋯ → Create purchase bill. Lines are copied; change quantities to what actually came.",
        "Or directly: Purchases → New purchase → Document: Purchase bill → Supplier (F4).",
        "Supplier bill no.: type the number printed on their bill (required; duplicates are refused).",
        "For medicines (batch-tracked): type Batch no., Expiry date and MRP on each line, exactly as on the pack.",
        "Payment made: tap Cash/UPI/Bank if you paid now, otherwise leave it as owed.",
        "Save & finalize. Stock increases at the purchase cost; GST input credit and supplier dues are recorded.",
      ] },
      { heading: "Return goods to the supplier (purchase return / debit note)", steps: [
        "Open the purchase bill → ⋯ → Create purchase return → keep only the items going back → Finalize.",
        "Expired stock: Products & Inventory → Expiry → Return to supplier → Draft debit note (it finds the right bill for you).",
      ] },
      { heading: "Pay the supplier", steps: ["Banking & Payments → Record payment → Paid out → supplier → pick the bills it settles."] },
    ],
    tips: ["A purchase bill cannot be edited after finalizing; return goods with a purchase return, or ⋯ → Cancel document with a reason."],
    related: ["inventory", "reorder", "payments"],
  },

  // ------------------------------------------------------------------ stock
  {
    id: "field", title: "Field sales: beats, salesman's day, targets", group: "Selling", routes: ["/field"],
    summary: "Give each salesman a route (beat) of customers; on their phone they take orders, collect money and note every visit; you see targets vs achievement.",
    sections: [
      { heading: "Set up (manager)", steps: [
        "Settings → Modules → switch on Field sales.",
        "Users & Permissions → invite each salesman (role Sales executive). If they collect money, also give them “Record payments”.",
        "Field sales → Beats → New beat: name, day of the week, salesman, and the customers in visiting order.",
        "Field sales → Targets → Set target per salesman per month (sales and collection).",
      ] },
      { heading: "The salesman's day (on the phone)", steps: [
        "Field sales → My day shows today's beat: each shop with its dues, last visit, call / WhatsApp / map.",
        "Order: search products, set quantities, Book order — a sales order with them as salesperson.",
        "Collect: record cash / UPI / cheque against the shop's bills.",
        "Note: outcome (order, payment, no order, shop closed…), a note, next visit date; location is saved if allowed.",
        "A shop not on the beat? “Visit another customer”.",
      ] },
      { heading: "Following up", steps: [
        "Targets shows sales (invoices where they are the salesperson, less returns), orders, collections and visits, with “on pace / behind” for the days gone.",
        "Visits lists every visit of a day with time, outcome, note and a map link.",
        "Convert sales orders into invoices as usual (Sales → the order → Convert): the salesperson carries over.",
      ] },
    ],
    related: ["sales", "customers", "payments"],
  },
  {
    id: "purchase-import", title: "Import a supplier's bill (Excel / CSV)", group: "Buying", routes: ["/purchases/import"],
    summary: "Your distributor sends the bill as a file: upload it instead of typing every line. Items you match once are remembered for that supplier.",
    sections: [
      { heading: "Import", steps: [
        "Purchases → Import supplier bill → choose the supplier, enter their bill number and date.",
        "Choose the file. Columns are found by their usual names: Item name, Item code, Barcode, Qty, Free, Rate / PTR, Disc %, GST %, Batch, Expiry, MRP.",
        "Each line shows how it was matched: Remembered, Barcode, Item code or Same name.",
        "For an unmatched line pick from the suggestions or “Search all products”. Tick Skip for lines you don't want.",
        "Create draft bill → check it in the normal purchase screen → Save & finalize.",
      ] },
      { heading: "Good to know", steps: [
        "Free / scheme quantity becomes its own zero-rate line, so stock is right and the cost per unit drops.",
        "Expiry like 03/27 means the end of March 2027.",
        "The bill's Total line is skipped automatically.",
      ] },
    ],
    related: ["purchases"],
  },
  {
    id: "damage", title: "Damage, breakage and claims", group: "Stock", routes: ["/damage"],
    summary: "Broken bottles, leaking packs, transit damage: write the stock off and claim it from the supplier until they credit it.",
    sections: [
      { heading: "Record damage", steps: [
        "Damage & claims → Record damage → scan the product, choose godown and batch, quantity and reason.",
        "Name the supplier to claim it from them (amount defaults to its cost), or leave it empty to only write it off.",
        "Stock goes down at once and the cost goes to Stock adjustments (a loss).",
      ] },
      { heading: "When the supplier responds", steps: [
        "They send a credit note: Credit received → amount and their credit note number. You owe them that much less and the loss shrinks.",
        "They refuse: Rejected with the reason. The loss stays.",
      ] },
    ],
    tips: ["Attach the photo of the damage (Documents) and mention the claim number when you call the supplier."],
    related: ["inventory"],
  },
  {
    id: "stock-audit", title: "Stock audit with your phone", group: "Stock", routes: ["/stock-audit"],
    summary: "Count the shelves by scanning: the phone camera, a USB / Bluetooth scanner, or typing the code. Differences correct the stock.",
    sections: [
      { heading: "Count", steps: [
        "Stock audit → choose the godown. Every item and batch with stock is listed with what the books say.",
        "Scan each piece (one scan = one more), or type the count. − and + adjust it.",
        "The count is saved on the phone as you go, so a lost connection or a closed tab loses nothing.",
      ] },
      { heading: "Post", steps: [
        "Spot check: only the items you counted are corrected.",
        "Full audit: switch it on and anything you did not count is set to zero.",
        "Post count: the differences go to Stock adjustments, and the stock is now what you counted.",
      ] },
    ],
    tips: ["Camera scanning works in Chrome on Android. On iPhone use a Bluetooth scanner or type the code."],
    related: ["inventory", "labels"],
  },
  {
    id: "labels", title: "Barcode labels", group: "Stock", routes: ["/inventory/labels"],
    summary: "Print price stickers with barcodes on A4 sticker sheets (65, 40 or 24 per sheet) or on a thermal label printer.",
    sections: [
      { heading: "Print", steps: [
        "Products & inventory → Labels. Search or scan the products; the number of labels starts at the stock in hand.",
        "Choose the label size and what to show (shop name, product, price, MRP).",
        "On a half-used sheet, Skip used labels starts printing further down.",
        "Print. In the print window set scale to 100 % (no “fit to page”).",
      ] },
    ],
    tips: ["Products without a barcode print their SKU as the barcode, and billing scans it the same way."],
    related: ["inventory", "stock-audit"],
  },
  {
    id: "units", title: "Selling in packs and loose", group: "Stock", routes: ["/inventory"],
    summary: "Stock and price are kept per base unit; a pack (box, case, strip, bag) lets you bill in packs or loose too.",
    sections: [
      { heading: "Set it", steps: [
        "Edit the product → Bigger unit: units in one pack (e.g. 12) and its name (box).",
        "On a bill the line shows packs + loose, and +1 box adds a whole pack.",
      ] },
    ],
    related: ["inventory"],
  },
  {
    id: "scheme-claims", title: "Scheme claims from manufacturers", group: "Stock", routes: ["/pricing"],
    summary: "Free goods and scheme discounts you give on a company's behalf, claimed back from that company.",
    sections: [
      { heading: "Set up", steps: [
        "Pricing & schemes → edit a scheme → Claim it from: the manufacturer or distributor, and their share (100 % if they bear it all).",
      ] },
      { heading: "Claim", steps: [
        "Pricing & schemes → Scheme claims → pick the period. Free goods are valued at your purchase price, discounts at the discount given.",
        "View & claim shows every bill line; Make claim records it so the same lines are never claimed twice.",
        "When their credit note comes: Credit received. You owe them that much less, booked as scheme income.",
        "Rejected frees the lines to be claimed again.",
      ] },
    ],
    related: ["damage"],
  },
  {
    id: "sale-or-return", title: "Sale or return (consignment)", group: "Selling", routes: ["/sale-or-return"],
    summary: "Send goods on a delivery challan; invoice what the customer sells and take back what they don't.",
    sections: [
      { heading: "How it works", steps: [
        "Make a delivery challan for the goods you send. Stock leaves at once.",
        "When the customer tells you what sold, convert the challan to an invoice for that quantity.",
        "Sale or return shows, per challan line, what was sent, sold, returned and is still with them.",
        "Goods back: enter what came back; it returns to stock at the cost it left at.",
      ] },
    ],
    related: ["sales"],
  },
  {
    id: "import", title: "Moving from Tally, Busy or Marg", group: "Workspace", routes: ["/import"],
    summary: "Bring items, customers and suppliers — with opening balances and opening stock — from your old software.",
    sections: [
      { heading: "From Tally", steps: [
        "In Tally: Gateway → Export → Masters → All Masters, XML. Upload Master.xml in Import data → Tally XML.",
        "Choose the year start and godown to bring opening balances and stock, then Check file and Import.",
      ] },
      { heading: "From Busy, Marg, Vyapar or Excel", steps: [
        "Export the item list or party list to Excel. Upload it as it is in Import data → Excel / CSV.",
        "Column names like Item Name, Main Unit, Tax Category, Sale Price, Party Name, GST No and Station are understood.",
        "Check file shows every bad row; nothing is saved until all rows are good.",
      ] },
    ],
    related: ["inventory"],
  },
  {
    id: "loyalty", title: "Loyalty points", group: "Selling", routes: ["/loyalty"],
    summary: "Customers earn points on every bill and spend them at the counter — a simple way to bring them back.",
    sections: [
      { heading: "Set up", steps: [
        "Settings → Modules → switch on Loyalty points.",
        "Loyalty points → points per ₹100 billed (e.g. 1) and value of one point (e.g. ₹1) = 1% back. Optionally “use from” a minimum.",
      ] },
      { heading: "At the counter", steps: [
        "Choose the customer on the bill (walk-in sales don't earn points).",
        "If they have points, the Payment section shows “Use N points (₹x)”: it pays that part and puts the rest in cash — change the mode if they pay by UPI.",
        "Points are earned on what they paid other than with points.",
      ] },
      { heading: "Corrections", steps: [
        "Cancelling a bill takes its points back; cancelling a points receipt returns them.",
        "Adjust (Loyalty points page) adds a welcome or birthday bonus, or removes points with a minus sign.",
      ] },
    ],
    related: ["sales", "customers"],
  },
  {
    id: "inventory", title: "Products and stock", group: "Stock", routes: ["/inventory"],
    summary: "Your catalogue and live stock by warehouse and batch. Stock changes only through documents, so it always adds up.",
    sections: [
      { heading: "Add a product", steps: [
        "Products & Inventory → New product → Name, SKU, Unit, HSN, GST rate, Selling price, Purchase price, MRP.",
        "Switches: Prices include GST, Track stock, Batches & expiry (medicines), Serial numbers (devices).",
        "Reorder level: the stock at which it shows under Alerts and Reorder.",
        "Batch/serial tracking cannot be changed after creation — decide when adding.",
      ] },
      { heading: "Stock in and out without a bill", steps: [
        "Opening stock: Movements tab → Opening stock.",
        "Adjust: damage, expiry write-off, samples, found stock — use a minus quantity to remove.",
        "Transfer: move stock between warehouses at its current cost.",
      ] },
      { heading: "Tabs", steps: [
        "Products — the list; click one for prices, stock, barcode label (Print) and movements.",
        "Stock — quantity and value by warehouse and batch.",
        "Alerts — below reorder level and expiring soon.",
        "Reorder — what to buy (see “Reorder”). Expiry — expired and expiring stock, returns to supplier. Movements — every stock document.",
      ] },
    ],
    related: ["expiry", "reorder", "purchases", "manufacturing"],
  },
  {
    id: "expiry", title: "Batches and expiry", group: "Stock", routes: ["/inventory?tab=expiry"],
    summary: "Medicines are sold earliest-expiry-first, expired batches can never be billed, and you are warned before stock expires.",
    sections: [
      { heading: "How it works", steps: [
        "On purchase bills you type Batch no., Expiry and MRP. Each batch keeps its own MRP.",
        "On sales, “Auto (first expiry)” picks the batch expiring first; if it is short, the bill shows one line per batch.",
        "An expired batch, or one on quality-control hold, is refused at billing.",
      ] },
      { heading: "Act before it expires", steps: [
        "Products & Inventory → Expiry: counts and value expired / within 30, 60, 90 days.",
        "Return to supplier: choose “Expired only” or “+ expiring in 30 days” → Draft debit note on the right bill → review → Finalize.",
        "Alerts: Automation → template “Expiry alert” e-mails you at 90, 60 and 30 days.",
      ] },
    ],
    related: ["inventory", "automation"],
  },
  {
    id: "reorder", title: "Reorder (what to buy)", group: "Stock", routes: ["/inventory?tab=reorder"],
    summary: "Suggested quantities from how fast each product sells, so you neither run out nor overstock.",
    sections: [
      { heading: "Use it", steps: [
        "Products & Inventory → Reorder.",
        "Set Sales of last 30 days, Lead (days the supplier takes) and Cover (days of stock you want).",
        "Suggested = daily sales × (lead + cover) − stock − already on order; never below the product’s reorder level.",
        "Untick what you don’t want, change quantities, choose a supplier for never-bought items → Create purchase orders.",
      ] },
    ],
    related: ["purchases"],
  },
  {
    id: "manufacturing", title: "Manufacturing (make your own products)", group: "Stock", routes: ["/manufacturing"],
    summary: "Recipes say what goes into a batch; recording production moves materials out and the finished batch in, at true cost.",
    sections: [
      { heading: "Create a recipe", steps: [
        "Manufacturing → Recipes → New recipe → finished product, Makes (batch size, e.g. 100 jars).",
        "Add materials with quantity per batch and wastage % (e.g. herb powder 25 kg + 4 %, jars 100).",
        "Overhead per batch: labour, fuel, packing. Shelf life sets the expiry. Tick quality control if batches need testing.",
      ] },
      { heading: "Record production", steps: [
        "Record production → recipe, quantity, warehouse, Batch number, MRP.",
        "The table shows materials needed vs in stock and the cost per unit. Buy shortages first.",
        "Record production: materials leave stock, the finished batch arrives at material cost + overhead.",
      ] },
      { heading: "Quality control", steps: [
        "Batches needing QC are on hold (cannot be sold). Production tab → click the run → write test results → Release, or Reject & write off.",
        "Wrong entry? Click the run → Cancel this production (while the batch is still fully in stock).",
      ] },
    ],
    related: ["inventory"],
  },

  // ------------------------------------------------------------------ money
  {
    id: "payments", title: "Payments received and paid", group: "Money", routes: ["/payments"],
    summary: "Money in from customers and money out to suppliers, matched against invoices and bills.",
    sections: [
      { heading: "Customer paid you", steps: [
        "Banking & Payments → Record payment → Received → customer → mode (cash/UPI/card/bank/cheque) and amount.",
        "Tick the invoices it settles (oldest first is suggested). Any extra stays as an advance.",
        "Or from the invoice itself: Record payment.",
      ] },
      { heading: "You paid a supplier", steps: ["Record payment → Paid out → supplier → amount → tick the bills."] },
      { heading: "Mistake?", steps: ["Click Cancel on the payment’s row and give a reason. The invoices it settled become due again; the payment stays on record as cancelled."] },
    ],
    related: ["sales", "purchases", "accounting"],
  },
  {
    id: "cheques", title: "Cheques: post-dated register and printing", group: "Money", routes: ["/cheques"],
    summary: "Keep every post-dated cheque in one list. Nothing goes into the books until the bank clears it, and a bounce undoes the receipt.",
    sections: [
      { heading: "A customer gives you a post-dated cheque", steps: [
        "Cheques → Cheque received: customer, cheque number, date written on it, their bank and the amount.",
        "Tick the bills it pays (or leave them: it becomes an advance when it clears).",
        "On its date you get an alert (set up an Automation rule on “A post-dated cheque's date arrives”) — deposit it.",
        "When the bank credits it: Cleared → date. The receipt is recorded and the bills are marked paid.",
      ] },
      { heading: "It bounced", steps: [
        "Press Bounced and give the reason (e.g. “Funds insufficient”).",
        "If it had cleared, the receipt is cancelled, so the bills are due again. The bounce stays on record.",
      ] },
      { heading: "Issuing a cheque to a supplier", steps: [
        "Cheques → Cheque issued: supplier, number, date, amount and the bills it pays.",
        "Print puts the date, payee, amount in words and figures and “A/C PAYEE” on the bank's cheque leaf.",
        "When it is debited in the bank: Cleared → the payment is recorded.",
      ] },
      { heading: "Getting the print to fit your cheque book", steps: [
        "Cheques → Print layout → Test print, on plain paper.",
        "Hold it against a cheque leaf. If everything is a little off, change only “Shift right / Shift down” (in mm).",
        "Save the layout. Load the cheque in the printer's manual / envelope tray.",
      ] },
    ],
    tips: ["Pending cheques are not in the cash book or the customer's balance until cleared — just as the bank sees it."],
    related: ["payments"],
  },
  {
    id: "tds", title: "TDS: deduct, record and deposit", group: "Money", routes: ["/tds"],
    summary: "Deduct TDS when paying contractors, professionals, rent or commission; record TDS your customers deduct; see what to deposit each month.",
    sections: [
      { heading: "Set up once", steps: [
        "On each supplier you deduct TDS from: Edit → PAN and Usual TDS section (e.g. 194C for transport).",
        "Without a PAN the rate is 20% — the TDS page flags a missing PAN in red.",
      ] },
      { heading: "Paying with TDS", steps: [
        "Banking & Payments → Record payment → supplier. “Deduct TDS” is ticked with their section.",
        "Tick the bills you are paying, then press “Split bill amount at x%”: the TDS and the amount to pay fill in.",
        "Example: bill ₹10,000 at 2% → pay ₹9,800, TDS ₹200. The bill is fully settled; ₹200 goes to TDS Payable.",
      ] },
      { heading: "When a customer deducts TDS", steps: [
        "Record the receipt for the money that arrived, tick “Customer deducted TDS” and enter their TDS and section.",
        "The invoice is fully settled; the TDS goes to TDS Receivable — you claim it in your income-tax return (check Form 26AS).",
      ] },
      { heading: "Depositing", steps: [
        "TDS → Deducted by us → Month by month shows what is due by the 7th (March: 30 April).",
        "Pay on the income-tax portal (challan ITNS 281), then Record challan with the serial no. and BSR code.",
        "CSV for the return exports the entries for your accountant's quarterly return (24Q / 26Q).",
      ] },
    ],
    tips: ["Rates and limits change with the Budget each year: the suggested rates only pre-fill the form; check them."],
    related: ["payments", "accounting"],
  },
  {
    id: "interest", title: "Interest on overdue bills", group: "Money", routes: ["/interest"],
    summary: "Charge interest to customers who pay late — calculated day by day on what was actually unpaid, and billed with a debit note.",
    sections: [
      { heading: "Set it up", steps: [
        "Settings → Modules → switch on “Interest on overdue bills”.",
        "Interest on dues → set the default yearly rate (e.g. 18%) and grace days after the due date.",
        "A customer can have their own rate on their page; 0% exempts them (e.g. a hospital you never charge).",
      ] },
      { heading: "Charge it", steps: [
        "The page lists each customer with each late invoice, the days and the interest. A part payment stops interest on that part from its date.",
        "Draft debit notes creates one draft per invoice. Review it in Sales, then finalize and send it.",
        "Interest is never charged twice: the next calculation starts the day after the last debit note.",
      ] },
    ],
    tips: ["Mention the interest rate in your invoice terms (Settings → Print templates) so customers know in advance."],
    related: ["sales", "customers"],
  },
  {
    id: "accounting", title: "Accounting and expenses", group: "Money", routes: ["/accounting"],
    summary: "Double-entry books written automatically by every bill, payment and stock movement.",
    sections: [
      { heading: "Record an expense (rent, electricity, salary advance…)", steps: [
        "Accounting → Record expense → date, expense head, amount, GST if any, paid from (cash/bank) or owed to a supplier.",
      ] },
      { heading: "Manual journal", steps: ["For contra (cash deposited in bank), capital, loans, accruals: Manual journal → debit and credit lines must be equal."] },
      { heading: "Give your CA the data", steps: [
        "Accounting → Tally → Masters (ledgers & parties), then Vouchers — this financial year. Import both in Tally.",
        "Reports → Trial balance / Day book / Profit & loss → Export CSV.",
      ] },
    ],
    related: ["reports", "claims"],
  },
  {
    id: "accounts-extra", title: "Budgets, cash closing, fixed assets, balance confirmation", group: "Money", routes: [],
    summary: "Monthly budgets vs actual, counting the cash drawer every evening, the asset register with tax depreciation, and ledger confirmations by link.",
    sections: [
      { heading: "Budgets (Accounting → Budgets)", steps: [
        "“Set budget for an account” → e.g. Rent → type the first month → “Same every month” → Save.",
        "Each month shows budget, actual from the books and the difference (red when an expense is over budget or income is under).",
      ] },
      { heading: "Daily cash closing (Accounting → Cash closing)", steps: [
        "At closing time, count the notes in the drawer and type how many of each, plus coins.",
        "The screen compares with what the books say should be there. Close the day: any shortage or excess is booked, so tomorrow starts right.",
      ] },
      { heading: "Fixed assets (Accounting → Fixed assets)", steps: [
        "Add asset: cost, date, book depreciation (WDV % or life), its Income-tax block, and where it was paid from.",
        "“Post depreciation to last month-end” books depreciation for every asset.",
        "The Income-tax table gives block-wise depreciation for your tax return (half rate for assets used less than 180 days).",
      ] },
      { heading: "Balance confirmation (customer / supplier page)", steps: [
        "Send on WhatsApp: they get a link with the statement and tap Confirm, or “Doesn't match” with their figure and the reason.",
        "Replies appear on the party page — useful at year end and for the auditor.",
      ] },
      { heading: "Payment links (invoice page)", steps: [
        "Connect Razorpay or Cashfree once under Integrations.",
        "On an unpaid invoice press Payment link: it is copied; share it on WhatsApp. When the customer pays, the payment is recorded automatically.",
      ] },
    ],
    related: ["accounting", "payments"],
  },
  {
    id: "claims", title: "Expense claims and petty cash", group: "Money", routes: ["/claims"],
    summary: "Staff claim money they spent for the business with a photo of the bill; you approve and pay.",
    sections: [
      { heading: "Staff: make a claim", steps: ["Expense claims → New claim → date, amount, type of expense, what for → Bill photo (opens the camera) → Submit claim."] },
      { heading: "Approver: decide", steps: [
        "Waiting claims are listed first. Click one → choose Pay from (Petty Cash, cash or bank) → Approve & pay, or write a note → Reject.",
        "Paid claims become normal expenses in the books, with the bill photo attached.",
      ] },
      { heading: "Petty cash", steps: ["Petty cash card → Top up from the till or bank. The balance and claims waiting are shown."] },
    ],
    related: ["accounting", "staff"],
  },
  {
    id: "staff", title: "Staff, attendance and salary", group: "Money", routes: ["/staff"],
    summary: "Employees, attendance by exception, and monthly salaries with commission, posted to the books.",
    sections: [
      { heading: "PF, ESI, professional tax and TDS", steps: [
        "Employees → Edit → Statutory deductions: switch on PF (with UAN and the basic + DA share), ESI (with IP number), choose the professional-tax state, and TDS if their salary is taxable.",
        "Calculate salaries: PF 12% (employer also 12%, of which 8.33% to pension on up to ₹15,000), ESI 0.75% / 3.25% on wages up to ₹21,000, PT by state slab, and TDS under the new regime.",
        "Change TDS on a line if the employee's actual tax differs (e.g. arrears).",
        "Post to books: the employer's PF/ESI is an expense; PF, ESI, PT and TDS payables are created.",
        "Below the table: amounts to deposit and due dates; ECR file downloads the PF return for the EPFO portal. Payslip prints each employee's slip.",
      ], note: "Rates are those notified for 2025-26. Other states' professional tax: enter it under Other deductions." },
      { heading: "Add employees", steps: [
        "Staff & Salary → Employees → Add employee → monthly salary or daily wage, joining date, bank details.",
        "Commission: set % and link their ERP login — they earn it on invoices where they are the salesperson.",
      ] },
      { heading: "Attendance", steps: [
        "Attendance tab → click a day to cycle: A absent (unpaid) → ½ half day → L paid leave → H holiday → present.",
        "Only mark exceptions; unmarked days count as present.",
      ] },
      { heading: "Pay salaries", steps: [
        "Salaries → choose the month → Calculate salaries (prorated by paid days, plus commission).",
        "Type Bonus and Deductions (advance recovery) per person.",
        "Post to books → then choose cash/bank → Mark paid.",
      ] },
    ],
    related: ["claims"],
  },
  {
    id: "gst", title: "GST returns and e-way bills", group: "Money", routes: ["/gst"],
    summary: "Returns are prepared from your finalized documents — check them here, then file on the GST portal.",
    sections: [
      { heading: "Monthly returns", steps: [
        "GST & Tax → pick the month (top right).",
        "GSTR-1: B2B, B2C, credit notes, HSN summary — download JSON for the portal or check line by line.",
        "GSTR-3B: output tax, input credit, net payable.",
        "Sales and Purchase registers for reconciliation with your CA.",
      ] },
      { heading: "E-way bill (goods over ₹50,000 moving)", steps: [
        "GST & Tax → E-way bills: invoices needing one are listed.",
        "Enter Distance, Vehicle no. (or Transporter ID) → tick invoices → Portal JSON.",
        "On ewaybillgst.gov.in: Generate → Bulk → upload the file.",
        "Type the 12-digit number back here → Save. It prints on the invoice with its validity.",
      ] },
    ],
    related: ["sales", "reports"],
  },
  {
    id: "gst-compliance", title: "GSTR-2B match, GSTR-9, composition, RCM and job work", group: "Money", routes: [],
    summary: "Monthly ITC check against GSTR-2B, the annual return, composition returns, reverse-charge self-invoices and job-work challans.",
    sections: [
      { heading: "GSTR-2B reconciliation (every month, before GSTR-3B)", steps: [
        "GST portal → Returns → GSTR-2B → choose the month → Download JSON.",
        "GST & tax → choose the same period → 2B reconcile → Upload GSTR-2B JSON.",
        "“Not in 2B — ITC at risk”: you recorded the bill but the supplier hasn't filed it. Call the supplier before claiming that ITC.",
        "“Amount differs”: compare the bill. “Not in your books”: record the purchase, or fix its bill number.",
      ] },
      { heading: "Annual and composition", steps: [
        "Annual / composition → GSTR-9 tables from your books; compare with the portal's pre-filled figures.",
        "If you are a composition dealer: choose your rate; CMP-08 tax per quarter (due the 18th after the quarter) and GSTR-4 totals (due 30 June).",
      ] },
      { heading: "Reverse charge from unregistered suppliers", steps: [
        "Record the purchase with “Reverse charge” ticked.",
        "RCM self-invoice → Make self-invoice → print it and keep it with the bill.",
      ] },
      { heading: "Job work", steps: [
        "Job work → Send to job worker: the job worker (a supplier), goods and quantities, the process. Stock moves to their godown; no GST is charged.",
        "When goods come back: Receive back on that challan.",
        "Inputs must return within 1 year (capital goods 3 years) — overdue ones are flagged. Download the ITC-04 CSV for the return.",
      ] },
    ],
    related: ["gst", "tds"],
  },
  {
    id: "reports", title: "Reports and scheduled e-mails", group: "Money", routes: ["/reports"],
    summary: "Profit & loss, balance sheet, cash flow, trial balance, day book, receivables/payables aging and stock valuation.",
    sections: [
      { heading: "Read a report", steps: ["Pick it on the left → choose the period → Print or Export CSV."] },
      { heading: "Get them by e-mail", steps: [
        "Reports → Scheduled e-mails → New schedule → daily/weekly/monthly, time, recipients, sections (sales, cash, dues, low stock, expiry…).",
        "Preview to see it, Send now to test. Needs Gmail/SMTP under Integrations.",
      ] },
    ],
    related: ["owner", "accounting"],
  },
  {
    id: "owner", title: "All businesses (owner)", group: "Money", routes: ["/owner"],
    summary: "Every business side by side: sales, profit, cash and bank, dues and stock value, with totals.",
    sections: [{ heading: "Use it", steps: ["Choose Today, This month or This financial year at the top.", "Click a business row to open its dashboard."] }],
  },

  // ------------------------------------------------------------------ workspace
  {
    id: "documents-library", title: "Documents (file library)", group: "Workspace", routes: ["/documents"],
    summary: "Your own files — licences, supplier bills, prescriptions, agreements — in folders, with versions and trash.",
    sections: [
      { heading: "Use it", steps: [
        "Upload or drag files onto the list. New folder to organise. Click a file to preview, rename, tag, add a new version or remove it.",
        "Files attached on an invoice/customer/product appear here too, visible only to people who may see that record.",
        "Trash keeps removed files until you Delete for ever.",
      ] },
    ],
  },
  {
    id: "licences", title: "Licences and compliance", group: "Workspace", routes: ["/licences"],
    summary: "Drug, FSSAI, AYUSH, trade and other licences, printed on invoices with renewal reminders.",
    sections: [
      { heading: "Use it", steps: [
        "Licences → Add licence → type, number, issued by, valid until, upload the certificate.",
        "Print number on invoices: shown under your GSTIN on every bill while valid.",
        "Renewal reminders: Automation → template “Licence renewal” (90, 60, 30 days before).",
      ] },
    ],
  },
  {
    id: "automation", title: "Automation (your own rules)", group: "Workspace", routes: ["/automation"],
    summary: "“When this happens, do that” — reminders, alerts and follow-ups that run by themselves.",
    sections: [
      { heading: "Make a rule", steps: [
        "Automation → click a template (Overdue reminder, Big-sale alert, Expiry alert, Licence renewal, New online order…) or New rule.",
        "When: the event. Only if: conditions (e.g. total ≥ 50000). Wait: delay. Then: send the invoice to the customer or e-mail you.",
        "Create rule. It works on new events from now on; Run history shows every run and why one was skipped or failed.",
      ] },
    ],
    tips: ["Messages need an e-mail/WhatsApp/SMS connection under Integrations."],
  },
  {
    id: "integrations", title: "Integrations", group: "Workspace", routes: ["/integrations"],
    summary: "Connect e-mail, WhatsApp, SMS, payment gateways, Google Drive backup and online stores.",
    sections: [{ heading: "Connect", steps: ["Integrations → pick the provider → Connect → enter its keys or Authorise with Google → Test.", "Logs show every call if something fails."] }],
  },
  {
    id: "developer", title: "Developer API and webhooks", group: "Workspace", routes: ["/developer"],
    summary: "Let your other software read and write data safely.",
    sections: [{ heading: "Use it", steps: ["New application → New key (shown once) with only the scopes needed.", "Webhooks → Add endpoint to be notified of events; Test sends a sample."] }],
  },
  {
    id: "users", title: "Users and permissions", group: "Workspace", routes: ["/users"],
    summary: "Who can do what, in which branch.",
    sections: [
      { heading: "Add a person", steps: [
        "Users & Permissions → Invite member → e-mail, name, role, branches → send them the invite link.",
        "Roles tab: use the built-in roles or make your own by ticking permissions.",
        "Activity tab: who did what and when.",
      ] },
      { heading: "Someone leaves", steps: ["Open the member → Suspend: their sessions and API keys stop at once."] },
    ],
  },
  {
    id: "businesses", title: "Businesses", group: "Workspace", routes: ["/businesses"],
    summary: "Each business has its own GSTIN, books, stock, customers and numbering.",
    sections: [{ heading: "Use it", steps: ["New business to add one; switch between them at the top of the sidebar.", "Archive hides a closed business without deleting its history."] }],
  },
  {
    id: "settings", title: "Settings", group: "Workspace", routes: ["/settings"],
    summary: "Business profile, branches, number series, print templates, modules and system (backups).",
    sections: [{ heading: "Sections", steps: [
      "Business: name, GSTIN, address, logo, bank/UPI for invoices.",
      "Branches: Add branch for each shop/godown.",
      "Numbering: New series for different counters or document kinds.",
      "Print templates: design A4 and thermal invoices (drag fields, logo, custom fields, preview).",
      "Modules: switch on only what this business uses — pharmacy, manufacturing, price lists, WhatsApp catalogue, staff & salary, expense claims, licences, automation, developer API. A quick set-up button per business type sets them in one go. Switching off only hides a module; its data is kept.",
      "System: backups (Back up now), health, security (2FA).",
    ] }],
  },
];

export const HELP_BY_ID = new Map(HELP.map((t) => [t.id, t]));

/** The guide for the page at ``path`` (the most specific route wins). */
export function topicForPath(path: string, search = ""): HelpTopic | undefined {
  const full = search ? `${path}?${search}` : path;
  let best: HelpTopic | undefined;
  let len = -1;
  for (const t of HELP) {
    for (const r of t.routes) {
      const hit = r.includes("?") ? full.startsWith(r) : r.endsWith("/") ? path.startsWith(r) : path === r || path.startsWith(`${r}/`);
      if (hit && r.length > len) { best = t; len = r.length; }
    }
  }
  return best;
}
