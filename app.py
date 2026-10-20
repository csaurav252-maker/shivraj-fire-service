import datetime
import urllib.parse
import streamlit as st
import pandas as pd
import os
import json
from supabase import create_client, Client

# --- SUPABASE CONFIGURATION ---
SUPABASE_URL = "https://fwlckedxrtkymwqbegos.supabase.co"
SUPABASE_KEY = "sb_publishable_UJinMiq1Fln8ckiLH0cvlA_2ApxVLLS"

@st.cache_resource
def init_supabase():
    try:
        return create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception as e:
        return None

supabase: Client = init_supabase()

# --- DEFAULT SEEDING FOR FIRE SERVICE ---
def seed_default_fire_products():
    if not supabase: return
    try:
        res = supabase.table("fire_products").select("pid").execute()
        if not res.data:
            defaults = [
                {"pid": "FS101", "name": "ABC Powder Extinguisher 4KG", "category": "Extinguisher", "cost_price": 800.0, "price_per_unit": 1400.0, "stock_qty": 50.0},
                {"pid": "FS102", "name": "CO2 Fire Extinguisher 4.5KG", "category": "Extinguisher", "cost_price": 2200.0, "price_per_unit": 3500.0, "stock_qty": 30.0},
                {"pid": "FS103", "name": "Fire Hose Pipe 15 Mtr", "category": "Hose Pipe", "cost_price": 1200.0, "price_per_unit": 2000.0, "stock_qty": 20.0},
                {"pid": "FS104", "name": "Smoke Detector Alarm", "category": "Alarm System", "cost_price": 350.0, "price_per_unit": 750.0, "stock_qty": 100.0},
            ]
            supabase.table("fire_products").insert(defaults).execute()
    except:
        pass

seed_default_fire_products()

st.set_page_config(page_title="Shivraj Fire Service | Malkapur", page_icon="🧯", layout="wide")

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
                "cost_price": float(r["cost_price"]),
                "price_per_unit": float(r["price_per_unit"]),
                "stock_qty": float(r["stock_qty"])
            }
        return prods
    except:
        return {}

def update_db_stock(pid, new_stock):
    if not supabase: return
    try:
        supabase.table("fire_products").update({"stock_qty": float(new_stock)}).eq("pid", pid).execute()
    except:
        pass

def add_db_product(pid, name, category, cost_price, price_per_unit, stock_qty):
    if not supabase: return False
    try:
        data = {
            "pid": str(pid),
            "name": str(name),
            "category": str(category),
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
            
            logs.append({
                "id": r["id"], "time": r["time"], "buyer": r["buyer"], "phone": r["phone"], "location": r["location"],
                "items": items_parsed, "amount": float(r["amount"]), "paid_amount": float(r["paid_amount"]), 
                "profit": float(r["profit"]), "balance_due": float(r["balance_due"]), 
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
            "items": json.dumps(log_data['items']), "amount": log_data['amount'], "paid_amount": log_data['paid_amount'],
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

def delete_db_expense(exp_id):
    if not supabase: return
    try:
        supabase.table("fire_expenses").delete().eq("id", exp_id).execute()
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
        cart_total = 0.0
        for index, c_item in enumerate(st.session_state.cart):
            item_cost = float(c_item['price']) * float(c_item['qty'])
            cart_total += item_cost
            st.markdown(f"**{index+1}. {c_item['name']}**\n{c_item['qty']} Qty x ₹{c_item['price']:,.2f} = **₹{item_cost:,.2f}**")

        if st.button("🗑️ Clear Cart"):
            st.session_state.cart = []
            st.rerun()

        st.markdown(f"### **Grand Total: ₹{cart_total:,.2f}**")
        st.divider()
        
        st.subheader("📝 Order & Payment Details")
        buyer_name = st.text_input("Customer / Company Name:").strip()
        buyer_phone = st.text_input("WhatsApp Phone Number:").strip()
        location = st.text_input("Site / Location Address:").strip()
        
        payment_mode = st.selectbox("Payment Method:", ["Cash", "UPI / GPay / PhonePe", "Bank Transfer", "Cheque"])
        paid_amount = float(st.number_input("Amount Paid Now (₹):", min_value=0.0, max_value=float(cart_total), value=float(cart_total)))
        credit_amount = float(max(0.0, cart_total - paid_amount))

        if st.button("Confirm Order & Generate Invoice", type="primary", use_container_width=True):
            if not buyer_name or not location:
                st.error("❌ Please fill in Customer Name and Location Address.")
            elif not st.session_state.cart:
                st.error("❌ Cart is empty.")
            else:
                current_prods = get_db_products()
                stock_error = False
                for c_item in st.session_state.cart:
                    if float(c_item['qty']) > float(current_prods[c_item['pid']]['stock_qty']):
                        st.error(f"❌ Stock insufficient for {c_item['name']}!")
                        stock_error = True
                        break

                if not stock_error:
                    total_profit = sum((float(c_item['price']) - float(current_prods[c_item['pid']]['cost_price'])) * float(c_item['qty']) for c_item in st.session_state.cart)

                    for c_item in st.session_state.cart:
                        update_db_stock(c_item['pid'], current_prods[c_item['pid']]['stock_qty'] - float(c_item['qty']))

                    timestamp = datetime.datetime.now().strftime("%d-%m-%Y %H:%M")
                    items_str = "\n".join([f"- {it['name']} ({it['qty']} Nos @ ₹{it['price']})" for it in st.session_state.cart])
                    payment_desc = f"{payment_mode} (Paid: ₹{paid_amount:,.2f})"

                    wa_msg = (
                        f"🧯 *SHIVRAJ FIRE SERVICE* 🧯\n"
                        f"📍 *Address:* Laxmi Nagar, Near Old BSNL Office,\n"
                        f"Malkapur, Dist. Buldhana\n"
                        f"👥 *Prop:* Swapnil Deshmukh & Ganesh Deshmukh\n"
                        f"📞 *Contact:* 9011763817 (Ganesh Deshmukh)\n"
                        f"--------------------------------\n"
                        f"📅 Date: {timestamp}\n"
                        f"👤 Customer: {buyer_name}\n"
                        f"📍 Site: {location}\n"
                        f"📦 Items / Services:\n{items_str}\n"
                        f"--------------------------------\n"
                        f"💰 Total Amount: ₹{cart_total:,.2f}\n"
                        f"💵 Paid Amount: ₹{paid_amount:,.2f}\n"
                        f"🔴 Balance Due: ₹{credit_amount:,.2f}\n"
                        f"--------------------------------\n"
                        f"Thank you for choosing Shivraj Fire Service! 🙏"
                    )

                    insert_db_log({
                        "time": timestamp, "buyer": buyer_name, "phone": buyer_phone, "location": location,
                        "items": list(st.session_state.cart), "amount": float(cart_total), "paid_amount": float(paid_amount),
                        "profit": float(total_profit), "balance_due": float(credit_amount), "payment": payment_desc,
                        "msg": wa_msg, "status": f"Pending (Due: ₹{credit_amount:,.2f})" if credit_amount > 0 else "Paid"
                    })

                    st.session_state.cart = []
                    st.success("✅ Order Invoice saved successfully!")
                    st.balloons()
                    st.rerun()

# --- HEADER SECTION WITH ADDRESS, OWNERS NAME & PHONE ---
st.title("🧯 SHIVRAJ FIRE SERVICE")
st.markdown("📍 **Address:** Laxmi Nagar, Near Old BSNL Office, Malkapur, Dist. Buldhana")
st.markdown("👥 **Proprietors:** Swapnil Deshmukh | Ganesh Deshmukh (📞 **9011763817**)")
st.caption("Complete Fire Safety Solutions, Equipment Sales & Refilling Management System")
st.divider()

current_logs = get_db_logs()
all_expenses = get_db_expenses()

total_sales = sum(l['amount'] for l in current_logs)
total_gross_profit = sum(l.get('profit', 0) for l in current_logs)
total_expenses_amt = sum(e['amount'] for e in all_expenses)
total_credit_due = sum(l['balance_due'] for l in current_logs)
net_profit = total_gross_profit - total_expenses_amt

# Top Metrics
m1, m2, m3, m4 = st.columns(4)
m1.metric("💰 Total Revenue", f"₹{total_sales:,.2f}")
m2.metric("📦 Total Orders", f"{len(current_logs)}")
m3.metric("🔴 Pending Credit (उधारी)", f"₹{total_credit_due:,.2f}", delta_color="inverse")
m4.metric("📈 True Net Profit", f"₹{net_profit:,.2f}", delta_color="normal" if net_profit >= 0 else "inverse")

st.divider()

# --- TABS CONTROL ---
tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "🧯 Fire Safety Catalog", 
    "📜 Sales & Billing Records",
    "📉 Credit Ledger (उधारी खाते)",
    "📈 Profit & Expenses",
    "⚙️ Inventory & Stock Control"
])

with tab1:
    st.subheader("Equipment Sales & Quick Booking")
    prods = get_db_products()
    if not prods:
        st.info("No fire safety items found.")
    else:
        cols = st.columns(3)
        for i, (pid, p) in enumerate(prods.items()):
            with cols[i % 3]:
                with st.container(border=True):
                    st.markdown(f"### **{p['name']}**")
                    st.caption(f"Category: `{p['category']}` | ID: `{pid}`")
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
                                st.session_state.cart.append({"pid": pid, "name": p['name'], "price": p['price_per_unit'], "qty": qty_inp})
                            st.success("Item added to cart!")
                            st.rerun()

with tab2:
    st.subheader("Sales History & WhatsApp Invoices")
    logs = get_db_logs()
    if not logs:
        st.info("No transaction logs available.")
    else:
        search = st.text_input("🔍 Search Customer Name / Location:", key="s_log").strip().lower()
        filtered = [l for l in logs if search in l['buyer'].lower() or search in l['location'].lower()]
        
        for log in reversed(filtered):
            with st.container(border=True):
                st.markdown(f"**Date:** `{log['time']}` | **Customer:** 👤 **{log['buyer']}** (`{log['location']}`) | **Status:** {log['status']}")
                st.markdown(f"💰 **Total Invoice:** ₹{log['amount']:,.2f} | 💵 **Paid:** ₹{log['paid_amount']:,.2f} | 🔴 **Due Balance:** **₹{log['balance_due']:,.2f}**")
                
                if log.get('phone'):
                    wa_url = f"https://wa.me/{log['phone']}?text={urllib.parse.quote(log['msg'])}"
                    st.markdown(f"📲 [Send Invoice via WhatsApp]({wa_url})")

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
                    st.markdown(f"👤 **Customer:** {p_log['buyer']} ({p_log['location']})")
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
            st.markdown("### **Record Business Expense**")
            with st.form("exp_form"):
                exp_date = st.text_input("Date:", value=datetime.datetime.now().strftime("%d-%m-%Y"))
                exp_cat = st.selectbox("Category:", ["Refilling Charges", "Transport & Fuel", "Labor / Technicians", "Office & Misc"])
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
    st.subheader("⚙️ Inventory Warehouse Control")
    admin_inv = st.text_input("Enter Admin Password:", type="password", key="pass_inv")
    if admin_inv == "admin123":
        ic1, ic2 = st.columns(2)
        with ic1:
            st.markdown("### **Add / Update Fire Equipment**")
            pid = st.text_input("Item ID (e.g. FS105):").strip().upper()
            pname = st.text_input("Equipment Name:").strip()
            pcat = st.selectbox("Category:", ["Extinguisher", "Hose Pipe", "Alarm System", "Refilling", "Spare Parts"])
            pcost = float(st.number_input("Cost Price (₹):", value=500.0))
            pprice = float(st.number_input("Selling Price (₹):", value=1000.0))
            pstock = float(st.number_input("Available Stock Quantity:", value=50.0))

            if st.button("Save Item to Database"):
                if pid and pname:
                    if add_db_product(pid, pname, pcat, pcost, pprice, pstock):
                        st.success(f"Product '{pname}' saved successfully!")
                        st.rerun()
                else:
                    st.error("Please fill Item ID and Name.")
        with ic2:
            st.markdown("### **Remove Equipment**")
            cur_prods = get_db_products()
            if cur_prods:
                del_id = st.selectbox("Select Equipment to Delete:", list(cur_prods.keys()))
                if st.button("Delete Item"):
                    delete_db_product(del_id)
                    st.success("Item removed successfully!")
                    st.rerun()
