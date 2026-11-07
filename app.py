import datetime
import urllib.parse
import streamlit as st
import pandas as pd
import json
from supabase import create_client, Client
import gst_invoice as gst   # GST tax calculation + "ORIGINAL FOR RECIPIENT" PDF

# --- SUPABASE CONFIGURATION (SECURE) ---
SUPABASE_URL = st.secrets["SUPABASE_URL"]
SUPABASE_KEY = st.secrets["SUPABASE_KEY"]
@st.cache_resource
def init_supabase():
    try:
        return create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception as e:
        return None

supabase: Client = init_supabase()

st.set_page_config(page_title="Shivraj Fire & Industrial Gases | Malkapur", page_icon="🧯", layout="wide")

# --- DATABASE HELPER FUNCTIONS ---
def get_db_products():
    if not supabase: return {}
    try:
        res = supabase.table("fire_products").select("*").execute()
        prods = {}
        for r in res.data:
            prods[r["pid"]] = {
                "name": r["name"],
                "category": r.get("category", "General"),
                "gas_type": r.get("gas_type", "N/A"),
                "capacity": r.get("capacity", "N/A"),
                "service_type": r.get("service_type", "Supply"),
                "cost_price": float(r.get("cost_price", 0)),
                "price_per_unit": float(r.get("price_per_unit", 0)),
                "stock_qty": float(r.get("stock_qty", 0)),
                # optional columns (leave empty to use defaults from gst_invoice.py)
                "hsn_code": r.get("hsn_code") or "",
                "gst_rate": r.get("gst_rate")
            }
        return prods
    except Exception as e:
        return {}

def update_db_stock(pid, new_stock):
    if not supabase: return
    try:
        supabase.table("fire_products").update({"stock_qty": float(new_stock)}).eq("pid", pid).execute()
    except:
        pass

def add_db_product(pid, name, category, gas_type, capacity, service_type, cost_price, price_per_unit, stock_qty):
    if not supabase: return False
    try:
        data = {
            "pid": str(pid),
            "name": str(name),
            "category": str(category),
            "gas_type": str(gas_type),
            "capacity": str(capacity),
            "service_type": str(service_type),
            "cost_price": float(cost_price),
            "price_per_unit": float(price_per_unit),
            "stock_qty": float(stock_qty)
        }
        supabase.table("fire_products").upsert(data).execute()
        return True
    except Exception as e:
        st.error(f"Error saving product: {e}")
        return False

def delete_db_product(pid):
    if not supabase: return
    try:
        supabase.table("fire_products").delete().eq("pid", pid).execute()
    except:
        pass

def get_db_logs():
    if not supabase: return []
    try:
        res = supabase.table("fire_sales_logs").select("*").order("id", desc=False).execute()
        logs = []
        for r in res.data:
            try:
                items_parsed = json.loads(r["items"]) if isinstance(r["items"], str) else r["items"]
            except:
                items_parsed = []

            # New bills store {"lines": [...], "meta": {...}}; old bills store just a list.
            meta_parsed = {}
            if isinstance(items_parsed, dict):
                meta_parsed = items_parsed.get("meta", {}) or {}
                items_parsed = items_parsed.get("lines", []) or []

            logs.append({
                "id": r["id"], "time": r["time"], "buyer": r["buyer"], "phone": r["phone"], "location": r["location"],
                "items": items_parsed, "meta": meta_parsed, "amount": float(r["amount"]), "paid_amount": float(r["paid_amount"]), 
                "profit": float(r.get("profit", 0)), "balance_due": float(r["balance_due"]), 
                "payment": r["payment"], "msg": r["msg"], "status": r["status"]
            })
        return logs
    except:
        return []

def insert_db_log(log_data):
    if not supabase: return
    try:
        payload = {
            "time": log_data['time'], "buyer": log_data['buyer'], "phone": log_data['phone'], "location": log_data['location'],
            "items": json.dumps({"lines": log_data['items'], "meta": log_data.get('meta', {})}), "amount": log_data['amount'], "paid_amount": log_data['paid_amount'],
            "profit": log_data['profit'], "balance_due": log_data['balance_due'], "payment": log_data['payment'],
            "msg": log_data['msg'], "status": log_data['status']
        }
        supabase.table("fire_sales_logs").insert(payload).execute()
    except Exception as e:
        st.error(f"Error saving sales log: {e}")

def delete_db_log(log_id):
    if not supabase: return
    try:
        supabase.table("fire_sales_logs").delete().eq("id", log_id).execute()
    except:
        pass

def update_db_credit_payment(log_id, new_paid_total, new_balance_due, status, payment_str):
    if not supabase: return
    try:
        supabase.table("fire_sales_logs").update({
            "paid_amount": new_paid_total, "balance_due": new_balance_due, "status": status, "payment": payment_str
        }).eq("id", log_id).execute()
    except:
        pass

def get_db_expenses():
    if not supabase: return []
    try:
        res = supabase.table("fire_expenses").select("*").execute()
        return [{"id": r["id"], "date": r["date"], "category": r["category"], "amount": float(r["amount"]), "description": r["description"]} for r in res.data]
    except:
        return []

def add_db_expense(date, category, amount, description):
    if not supabase: return
    try:
        supabase.table("fire_expenses").insert({"date": date, "category": category, "amount": float(amount), "description": description}).execute()
    except:
        pass

if "cart" not in st.session_state:
    st.session_state.cart = []

# --- SIDEBAR: BILLING CART & CHECKOUT ---
with st.sidebar:
    st.header("🛒 Sales & Refilling Cart")
    
    if not st.session_state.cart:
        st.info("Cart is currently empty.")
    else:
        for index, c_item in enumerate(st.session_state.cart):
            item_cost = float(c_item['price']) * float(c_item['qty'])
            st.markdown(f"**{index+1}. {c_item['name']}**\n[{c_item['capacity']} | {c_item['service_type']}]\n{c_item['qty']} Qty x ₹{c_item['price']:,.2f} = **₹{item_cost:,.2f}**")

        if st.button("🗑️ Clear Cart"):
            st.session_state.cart = []
            st.rerun()

        st.divider()

        # --- GST SETTINGS ---
        st.subheader("🧾 GST Details")
        price_mode_label = st.radio(
            "Item rates entered are:",
            ["Without GST (GST added on top)", "With GST (GST already included)"],
            help="Choose 'With GST' if your selling rates already include GST, then the bill total stays the same."
        )
        price_mode = "exclusive" if price_mode_label.startswith("Without") else "inclusive"

        state_options = [f"{code} - {name}" for code, name in gst.STATES.items()]
        seller_state_idx = list(gst.STATES.keys()).index(gst.SELLER["state_code"])
        cust_state = st.selectbox("Customer State (Place of Supply):", state_options, index=seller_state_idx)
        cust_state_code = cust_state.split(" - ")[0]
        buyer_gstin = st.text_input("Customer GSTIN (if registered):").strip().upper()
        is_intra = (cust_state_code == gst.SELLER["state_code"])

        if buyer_gstin and not gst.is_valid_gstin(buyer_gstin):
            st.warning("⚠️ GSTIN format looks wrong (15 characters needed).")
        elif buyer_gstin and buyer_gstin[:2] != cust_state_code:
            st.warning(f"⚠️ GSTIN starts with state code {buyer_gstin[:2]} but the selected state is {cust_state_code}.")

        # Resolve HSN / GST % for every cart line (product row override -> defaults) and calculate tax
        all_prods_for_gst = get_db_products()
        gst_lines = []
        for c_item in st.session_state.cart:
            prod = all_prods_for_gst.get(c_item['pid'], {})
            line = dict(c_item)
            line["category"] = prod.get("category", c_item.get("category", ""))
            line["gas_type"] = prod.get("gas_type", c_item.get("gas_type", ""))
            line["hsn"] = prod.get("hsn_code") or c_item.get("hsn") or ""
            line["gst_rate"] = prod.get("gst_rate") if prod.get("gst_rate") not in (None, "") else c_item.get("gst_rate")
            hsn_v, rate_v = gst.line_tax_info(line)
            line["hsn"], line["gst_rate"] = hsn_v, float(rate_v)
            gst_lines.append(line)

        inv_calc = gst.compute_invoice(gst_lines, price_mode, is_intra)
        cart_total = float(inv_calc["grand_total"])

        st.markdown(f"Taxable Value: ₹{float(inv_calc['taxable']):,.2f}")
        if is_intra:
            st.markdown(f"CGST: ₹{float(inv_calc['cgst']):,.2f}  \nSGST: ₹{float(inv_calc['sgst']):,.2f}")
        else:
            st.markdown(f"IGST: ₹{float(inv_calc['igst']):,.2f}")
        if inv_calc["round_off"] != 0:
            st.markdown(f"Round Off: ₹{float(inv_calc['round_off']):,.2f}")
        st.markdown(f"### **Grand Total: ₹{cart_total:,.2f}**")
        st.divider()

        st.subheader("📝 Customer & Site Details")
        buyer_name = st.text_input("Customer / Hospital / Factory Name:").strip()
        buyer_phone = st.text_input("WhatsApp Phone Number:").strip()
        location = st.text_input("Site / Plant / Address:").strip()

        payment_mode = st.selectbox("Payment Method:", ["Cash", "UPI / GPay / PhonePe", "Bank Transfer", "Cheque"])
        paid_amount = float(st.number_input("Amount Paid Now (₹):", min_value=0.0, max_value=float(cart_total), value=float(cart_total)))
        credit_amount = float(max(0.0, cart_total - paid_amount))

        if st.button("Confirm Order & Generate Invoice", type="primary", use_container_width=True):
            if not buyer_name or not location:
                st.error("❌ Please fill in Customer Name and Location Address.")
            elif not st.session_state.cart:
                st.error("❌ Cart is empty.")
            elif buyer_gstin and not gst.is_valid_gstin(buyer_gstin):
                st.error("❌ Customer GSTIN is not valid. Correct it or leave it empty for an unregistered customer.")
            else:
                current_prods = get_db_products()
                stock_error = False
                for c_item in st.session_state.cart:
                    if float(c_item['qty']) > float(current_prods[c_item['pid']]['stock_qty']):
                        st.error(f"❌ Stock insufficient for {c_item['name']}!")
                        stock_error = True
                        break

                if not stock_error:
                    # Profit is calculated on the taxable value (GST is not your income)
                    total_profit = sum(
                        float(inv_line['taxable']) - float(current_prods[c_item['pid']]['cost_price']) * float(c_item['qty'])
                        for c_item, inv_line in zip(st.session_state.cart, inv_calc['lines'])
                    )

                    for c_item in st.session_state.cart:
                        update_db_stock(c_item['pid'], current_prods[c_item['pid']]['stock_qty'] - float(c_item['qty']))

                    timestamp = datetime.datetime.now().strftime("%d-%m-%Y %H:%M")
                    next_refill_date = (datetime.datetime.now() + datetime.timedelta(days=365)).strftime("%d-%m-%Y")
                    
                    items_formatted = []
                    for inv_line in inv_calc['lines']:
                        items_formatted.append(
                            f"• *{inv_line['name']}*\n  [{inv_line['capacity']} | {inv_line['service_type']}]\n"
                            f"  └ Qty: {gst.fmt_qty(inv_line['qty'])} Nos @ ₹{float(inv_line['unit_rate']):,.2f} = ₹{float(inv_line['taxable']):,.2f}\n"
                            f"  └ HSN/SAC: {inv_line['hsn']} | GST: {inv_line['gst_rate'].normalize():f}%"
                        )

                    items_str = "\n\n".join(items_formatted)
                    payment_desc = f"{payment_mode} (Paid: ₹{paid_amount:,.2f})"

                    if is_intra:
                        tax_lines = (f"🧮 *CGST:* ₹{float(inv_calc['cgst']):,.2f}\n"
                                     f"🧮 *SGST:* ₹{float(inv_calc['sgst']):,.2f}\n")
                    else:
                        tax_lines = f"🧮 *IGST:* ₹{float(inv_calc['igst']):,.2f}\n"
                    seller_gstin_line = f"🆔 *GSTIN:* {gst.SELLER['gstin']}\n" if gst.SELLER["gstin"] else ""
                    buyer_gstin_line = f"🆔 *Client GSTIN:* {buyer_gstin}\n" if buyer_gstin else ""

                    wa_msg = (
                        f"====================================\n"
                        f"🧾 *TAX INVOICE*\n"
                        f"🧾 *SHIVRAJ FIRE SERVICE & INDUSTRIAL GASES*\n"
                        f"📍 *Address:* Laxmi Nagar, Near Old BSNL Office, Malkapur, Dist. Buldhana\n"
                        f"{seller_gstin_line}"
                        f"👥 *Prop:* Swapnil Deshmukh & Ganesh Deshmukh\n"
                        f"📞 *Contact:* 9011763817 (Ganesh Deshmukh)\n"
                        f"====================================\n\n"
                        f"📝 *INVOICE DETAILS*\n"
                        f"------------------------------------\n"
                        f"📅 *Date & Time:* {timestamp}\n"
                        f"👤 *Client Name:* {buyer_name}\n"
                        f"{buyer_gstin_line}"
                        f"📍 *Site Location:* {location}\n"
                        f"🏛️ *Place of Supply:* {gst.STATES.get(cust_state_code, '')} ({cust_state_code})\n"
                        f"📞 *Phone:* {buyer_phone if buyer_phone else 'N/A'}\n\n"
                        f"------------------------------------\n"
                        f"📦 *ITEMS & SERVICES SUPPLIED*\n"
                        f"------------------------------------\n"
                        f"{items_str}\n\n"
                        f"------------------------------------\n"
                        f"💰 *PAYMENT SUMMARY*\n"
                        f"------------------------------------\n"
                        f"💵 *Taxable Value:* ₹{float(inv_calc['taxable']):,.2f}\n"
                        f"{tax_lines}"
                        f"➕ *Round Off:* ₹{float(inv_calc['round_off']):,.2f}\n"
                        f"💵 *Grand Total:* ₹{cart_total:,.2f}\n"
                        f"✅ *Amount Paid:* ₹{paid_amount:,.2f}\n"
                        f"🔴 *Balance Due:* ₹{credit_amount:,.2f}\n"
                        f"💳 *Payment Mode:* {payment_mode}\n\n"
                        f"------------------------------------\n"
                        f"🛡️ *SERVICE & TESTING WARRANTY*\n"
                        f"------------------------------------\n"
                        f"🗓️ *Next Service / Refill Due Date:* {next_refill_date}\n"
                        f"📜 *Guarantee:* 1 Year Hydraulic & Safety Testing Warranty Valid.\n\n"
                        f"====================================\n"
                        f"🙏 *Thank you for choosing Shivraj Services!*\n"
                        f"For urgent gas refilling & support, call 9011763817."
                    )

                    insert_db_log({
                        "time": timestamp, "buyer": buyer_name, "phone": buyer_phone, "location": location,
                        "items": gst_lines,
                        "meta": {"gstin": buyer_gstin, "state_code": cust_state_code, "price_mode": price_mode},
                        "amount": float(cart_total), "paid_amount": float(paid_amount),
                        "profit": float(total_profit), "balance_due": float(credit_amount), "payment": payment_desc,
                        "msg": wa_msg, "status": f"Pending (Due: ₹{credit_amount:,.2f})" if credit_amount > 0 else "Paid"
                    })

                    st.session_state.cart = []
                    st.success("✅ Order Invoice saved successfully!")
                    st.balloons()
                    st.rerun()

# --- HEADER SECTION ---
st.title("🧯 SHIVRAJ FIRE SERVICE & INDUSTRIAL GASES 🏥")
st.markdown("📍 **Address:** Laxmi Nagar, Near Old BSNL Office, Malkapur, Dist. Buldhana")
st.markdown("👥 **Proprietors:** Swapnil Deshmukh | Ganesh Deshmukh (📞 **9011763817**)")
st.caption("Fire Safety Solutions, Medical Oxygen Supply & Industrial Gases Management")
if not gst.SELLER["gstin"]:
    st.warning("⚠️ Your shop GSTIN is not set. Open gst_invoice.py, fill SELLER → \"gstin\", then redeploy. Until then the invoice PDF shows a blank GSTIN line.")
st.divider()

current_logs = get_db_logs()
all_expenses = get_db_expenses()
all_products = get_db_products()

total_sales = sum(l['amount'] for l in current_logs)
total_gross_profit = sum(l.get('profit', 0) for l in current_logs)
total_expenses_amt = sum(e['amount'] for e in all_expenses)
total_credit_due = sum(l['balance_due'] for l in current_logs)
net_profit = total_gross_profit - total_expenses_amt
total_cylinders_stock = sum(p['stock_qty'] for p in all_products.values())

# --- FULL VISIBILITY METRICS FIX (... च्या समस्येवर तोडगा) ---
m1, m2, m3, m4, m5 = st.columns(5)
m1.markdown(f"**💰 Revenue**  \n### ₹{total_sales:,.2f}")
m2.markdown(f"**📦 Stock In Hand**  \n### {int(total_cylinders_stock)} Units")
m3.markdown(f"**📜 Orders**  \n### {len(current_logs)}")
m4.markdown(f"**🔴 Credit (उधारी)**  \n### ₹{total_credit_due:,.2f}")
m5.markdown(f"**📈 Net Profit**  \n### ₹{net_profit:,.2f}")

st.divider()

# --- TABS CONTROL ---
tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "🧯 Equipment & Gas Catalog", 
    "📜 Sales & Billing Records",
    "📉 Credit Ledger (उधारी खाते)",
    "📈 Profit & Expenses",
    "⚙️ Inventory & Cylinder Control"
])

with tab1:
    st.subheader("Equipment & Gas Booking Catalog")
    prods = get_db_products()
    if not prods:
        st.info("No items or gases found in inventory.")
    else:
        cat_filter = st.selectbox("🔍 Filter by Category:", ["All Categories", "🧯 Fire Safety Equipment", "🏥 Medical Oxygen Gases", "🏭 Industrial & Chemical Gases", "⚙️ Accessories & Spares"])
        
        filtered_prods = {}
        for pid, p in prods.items():
            if cat_filter == "All Categories" or cat_filter in p['category']:
                filtered_prods[pid] = p

        cols = st.columns(3)
        for i, (pid, p) in enumerate(filtered_prods.items()):
            with cols[i % 3]:
                with st.container(border=True):
                    st.markdown(f"### **{p['name']}**")
                    st.caption(f"Category: `{p['category']}` | ID: `{pid}`")
                    st.markdown(f"🧪 **Gas / Type:** `{p['gas_type']}` | ⚖️ **Capacity:** `{p['capacity']}`")
                    st.markdown(f"🔄 **Type:** `{p['service_type']}`")
                    st.markdown(f"💰 **Selling Rate:** ₹{p['price_per_unit']:,.2f}")
                    
                    if p['stock_qty'] <= 5:
                        st.markdown(f"⚠️ **Stock:** `{p['stock_qty']} Units` *(Low Stock Alert)*")
                    else:
                        st.markdown(f"📦 **Stock:** `{p['stock_qty']} Units`")

                    qty_inp = float(st.number_input("Quantity:", min_value=1.0, value=1.0, key=f"q_{pid}"))
                    if st.button("🛒 Add to Cart", key=f"btn_{pid}", use_container_width=True):
                        if qty_inp > p['stock_qty']:
                            st.error("Exceeds available stock quantity!")
                        else:
                            for item in st.session_state.cart:
                                if item['pid'] == pid:
                                    item['qty'] += qty_inp
                                    break
                            else:
                                st.session_state.cart.append({
                                    "pid": pid, "name": p['name'], "price": p['price_per_unit'], 
                                    "qty": qty_inp, "capacity": p['capacity'], "service_type": p['service_type'],
                                    "category": p['category'], "gas_type": p['gas_type']
                                })
                            st.success("Item added to cart!")
                            st.rerun()

with tab2:
    st.subheader("Sales History, Invoices & Reports")
    logs = get_db_logs()
    
    # --- EXPORT DATA BUTTONS (CSV / EXCEL) ---
    if logs:
        df_export = pd.DataFrame(logs)
        df_export_cleaned = df_export[['id', 'time', 'buyer', 'phone', 'location', 'amount', 'paid_amount', 'balance_due', 'status']]
        df_export_cleaned.columns = ['Invoice ID', 'Date', 'Customer Name', 'Phone', 'Location', 'Total Amount', 'Paid Amount', 'Balance Due', 'Payment Status']
        
        col_exp1, col_exp2 = st.columns([1, 4])
        with col_exp1:
            csv_data = df_export_cleaned.to_csv(index=False).encode('utf-8')
            st.download_button(
                label="📥 Export Sales Report (CSV)",
                data=csv_data,
                file_name=f"Shivraj_Fire_Sales_Report_{datetime.datetime.now().strftime('%d_%m_%Y')}.csv",
                mime="text/csv",
                use_container_width=True
            )
    st.divider()

    if not logs:
        st.info("No transaction logs available.")
    else:
        search = st.text_input("🔍 Search Customer / Hospital / Location:", key="s_log").strip().lower()
        filtered = [l for l in logs if search in l['buyer'].lower() or search in l['location'].lower()]
        
        for log in reversed(filtered):
            with st.container(border=True):
                c_head, c_del = st.columns([4, 1])
                with c_head:
                    st.markdown(f"**Invoice ID:** `#{log['id']}` | **Date:** `{log['time']}` | **Client:** 👤 **{log['buyer']}** (`{log['location']}`) | **Status:** {log['status']}")
                    st.markdown(f"💰 **Total Invoice:** ₹{log['amount']:,.2f} | 💵 **Paid:** ₹{log['paid_amount']:,.2f} | 🔴 **Due Balance:** **₹{log['balance_due']:,.2f}**")
                
                with c_del:
                    if st.button("🗑️ Delete Bill", key=f"del_log_{log['id']}", type="secondary"):
                        st.session_state[f"confirm_del_{log['id']}"] = True

                if st.session_state.get(f"confirm_del_{log['id']}", False):
                    del_pass = st.text_input(f"Enter Admin Password to delete Bill #{log['id']}:", type="password", key=f"p_del_{log['id']}")
                    if st.button("Confirm Delete", key=f"btn_cdel_{log['id']}"):
                        if del_pass == "admin123":
                            cur_prods = get_db_products()
                            for item in log['items']:
                                if item['pid'] in cur_prods:
                                    update_db_stock(item['pid'], cur_prods[item['pid']]['stock_qty'] + float(item['qty']))
                            
                            delete_db_log(log['id'])
                            st.success(f"Bill #{log['id']} deleted successfully and stock restored!")
                            st.rerun()
                        else:
                            st.error("Incorrect Password!")

                # --- VIEW & PRINTABLE BILL ---
                with st.expander(f"📄 View Full Invoice Copy (#{log['id']})"):
                    st.text(log['msg'])

                # --- GST TAX INVOICE PDF (ORIGINAL FOR RECIPIENT) ---
                pdf_key = f"pdf_bytes_{log['id']}"
                inv_no = gst.invoice_number(log['id'], log['time'])
                if pdf_key not in st.session_state:
                    if st.button("🖨️ Create GST Invoice PDF", key=f"mk_pdf_{log['id']}"):
                        try:
                            st.session_state[pdf_key] = gst.build_invoice_pdf(log)
                            st.rerun()
                        except Exception as e:
                            st.error(f"PDF could not be created: {e}")
                else:
                    st.download_button(
                        label=f"📥 Download Tax Invoice {inv_no} (ORIGINAL FOR RECIPIENT)",
                        data=st.session_state[pdf_key],
                        file_name=f"Invoice_{inv_no.replace('/', '-')}.pdf",
                        mime="application/pdf",
                        key=f"dl_pdf_{log['id']}"
                    )

                if log.get('phone'):
                    wa_url = f"https://wa.me/{log['phone']}?text={urllib.parse.quote(log['msg'])}"
                    st.markdown(f"📲 [Send Professional WhatsApp Invoice]({wa_url})")

with tab3:
    st.subheader("📉 Credit Ledger & Balance Dues (उधारी ट्रॅकिंग)")
    pending = [l for l in get_db_logs() if l['balance_due'] > 0]
    if not pending:
        st.success("🎉 All accounts clear! No pending balance due.")
    else:
        for p_log in pending:
            with st.container(border=True):
                c1, c2 = st.columns([2, 2])
                with c1:
                    st.markdown(f"👤 **Customer/Client:** {p_log['buyer']} ({p_log['location']})")
                    st.markdown(f"💰 Total Amount: ₹{p_log['amount']:,.2f} | Paid: ₹{p_log['paid_amount']:,.2f}")
                    st.markdown(f"🔴 **Outstanding Balance: ₹{p_log['balance_due']:,.2f}**")
                with c2:
                    p_amt = float(st.number_input("Received Payment (₹):", min_value=0.0, max_value=float(p_log['balance_due']), key=f"p_{p_log['id']}"))
                    if st.button("💵 Clear Partial Payment", key=f"pay_btn_{p_log['id']}"):
                        if p_amt > 0:
                            new_paid = p_log['paid_amount'] + p_amt
                            new_due = p_log['balance_due'] - p_amt
                            status = "Paid" if new_due <= 0 else f"Pending (Due: ₹{new_due:,.2f})"
                            pay_log = p_log['payment'] + f"\n-> Received ₹{p_amt:,.2f} on {datetime.datetime.now().strftime('%d-%m-%Y')}"
                            
                            update_db_credit_payment(p_log['id'], new_paid, new_due, status, pay_log)
                            st.success(f"Recorded ₹{p_amt}! Remaining: ₹{new_due}")
                            st.rerun()

with tab4:
    st.subheader("📈 Financial Profit & Expense Management")
    admin_p = st.text_input("Enter Admin Password:", type="password", key="pass_fin")
    if admin_p == "admin123":
        col_f1, col_f2 = st.columns(2)
        with col_f1:
            st.markdown("### **Record Expense**")
            with st.form("exp_form"):
                exp_date = st.text_input("Date:", value=datetime.datetime.now().strftime("%d-%m-%Y"))
                exp_cat = st.selectbox("Category:", ["Refilling & Gas Purchase", "Transport & Cylinder Freight", "Labor & Technicians", "Office & Misc"])
                exp_amt = float(st.number_input("Amount (₹):", min_value=0.0, value=500.0))
                exp_notes = st.text_input("Expense Details:")
                if st.form_submit_button("Save Expense"):
                    add_db_expense(exp_date, exp_cat, exp_amt, exp_notes)
                    st.success("Expense saved!")
                    st.rerun()

        with col_f2:
            st.markdown("### **Overall Profit Breakdown**")
            st.metric("Total Gross Sales Margin", f"₹{total_gross_profit:,.2f}")
            st.metric("Total Operational Expenses", f"₹{total_expenses_amt:,.2f}")
            st.metric("True Net Profit", f"₹{net_profit:,.2f}", delta_color="normal" if net_profit >= 0 else "inverse")

with tab5:
    st.subheader("⚙️ Inventory & Cylinder Control")
    admin_inv = st.text_input("Enter Admin Password:", type="password", key="pass_inv")
    if admin_inv == "admin123":
        ic1, ic2 = st.columns(2)
        with ic1:
            st.markdown("### **Add / Update Gas Cylinder or Equipment**")
            pid = st.text_input("Item ID (e.g. FS105 / MO101 / IG201):").strip().upper()
            pname = st.text_input("Product / Cylinder Name (e.g. Mechanical Foam / DCP Powder / ABC Extinguisher):").strip()
            pcat = st.selectbox("Category:", ["🧯 Fire Safety Equipment", "🏥 Medical Oxygen Gases", "🏭 Industrial & Chemical Gases", "⚙️ Accessories & Spares"])
            pgas = st.selectbox("Gas / Type:", ["ABC Powder", "BC Powder", "DCP Powder", "M-Foam (Mechanical Foam)", "CO2", "Water", "Medical Oxygen (O2)", "Nitrogen (N2)", "Hydrogen (H2)", "Argon", "Acetylene (DA)", "Chemical Mix / N/A"])
            pcap = st.selectbox("Capacity / Size:", ["1 KG / Litre", "2 KG", "4 KG", "4.5 KG", "5 KG", "6 KG", "9 KG", "25 KG", "25 Litre", "50 KG", "50 Litre", "10 Litre (B-Type)", "47 Litre (Jumbo D-Type)", "15 Mtr", "N/A"])
            pserv = st.selectbox("Service Type:", ["New Supply", "Refilling Service", "Cylinder Rental / Deposit"])
            
            pcost = float(st.number_input("Cost Price (₹):", value=500.0))
            pprice = float(st.number_input("Selling Rate (₹):", value=1000.0))
            pstock = float(st.number_input("Available Stock Quantity:", value=50.0))

            if st.button("Save Item to Database"):
                if pid and pname:
                    if add_db_product(pid, pname, pcat, pgas, pcap, pserv, pcost, pprice, pstock):
                        st.success(f"Item '{pname}' saved successfully!")
                        st.rerun()
                else:
                    st.error("Please fill Item ID and Name.")
        with ic2:
            st.markdown("### **Remove Equipment / Gas Item**")
            cur_prods = get_db_products()
            if cur_prods:
                del_id = st.selectbox("Select Equipment to Delete:", list(cur_prods.keys()))
                if st.button("Delete Item"):
                    delete_db_product(del_id)
                    st.success("Item removed successfully!")
                    st.rerun()
