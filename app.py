from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
import sqlite3

import streamlit as st

import db

st.set_page_config(page_title="إدارة المبيعات والتحصيل", page_icon="📊", layout="wide")
st.markdown("""
<style>
html, body, [class*="css"] { direction: rtl; text-align: right; }
[data-testid="stSidebar"] { direction: rtl; }
[data-testid="stMetric"] { direction: rtl; text-align: right; }
div[data-testid="stDataFrame"] { direction: rtl; }
.small-note {color:#666;font-size:.9rem}
</style>
""", unsafe_allow_html=True)


def money(x):
    try:
        return f"{float(x):,.0f}"
    except Exception:
        return str(x)


def num(x):
    try:
        return f"{float(x):,.1f}"
    except Exception:
        return str(x)


def rep_options():
    rs = db.reps()
    labels = {f"{r['name']} ({r['code']})": r["code"] for r in rs}
    return rs, labels


def customer_options():
    cs = db.customers()
    labels = {f"{c['account_no']} — {c['name']} — {c.get('region') or ''}": c for c in cs}
    return cs, labels


def product_options():
    ps = db.products()
    labels = {f"{p['name']} | {p['volume']:g} L | مخزون {p['current_stock']:g}": p for p in ps}
    return ps, labels


st.title("📊 تطبيق المبيعات والتحصيل")
meta = db.meta()
source = meta.get("source_filename", "")
if source:
    st.caption(f"نسخة MVP — البيانات المستوردة من: {source} | آخر مبيعات في المصدر: {meta.get('source_sales_max_date','—')}")

page = st.sidebar.radio("القائمة", [
    "لوحة التحكم", "المبيعات", "المقبوضات", "الزبائن", "المواد والمخزون", "عمر الدين", "المحصلة الشهرية", "خطط المندوبين", "التدقيق والنسخ الاحتياطي"
])

if page == "لوحة التحكم":
    st.subheader("لوحة التحكم")
    c1, c2, c3 = st.columns(3)
    default_start = datetime.strptime(meta.get("source_sales_min_date", "2026-01-01"), "%Y-%m-%d").date()
    default_end = max(date.today(), datetime.strptime(meta.get("source_sales_max_date", date.today().isoformat()), "%Y-%m-%d").date())
    start = c1.date_input("من تاريخ", value=default_start)
    end = c2.date_input("إلى تاريخ", value=default_end)
    _, labels = rep_options()
    rep_label = c3.selectbox("المندوب", ["الكل"] + list(labels.keys()))
    rep = None if rep_label == "الكل" else labels[rep_label]
    k = db.dashboard(start, end, rep)
    a,b,c,d,e = st.columns(5)
    a.metric("قيمة المبيعات", money(k["sales_value"]))
    b.metric("الليترات", num(k["liters"]))
    c.metric("التحصيل", money(k["payments"]))
    d.metric("عدد حركات البيع", int(k["sales_count"]))
    e.metric("الرصيد الحالي الكلي", money(k["balance"]))
    st.markdown("#### ملخص شهري حسب المندوب")
    st.dataframe(db.monthly_summary(), use_container_width=True, hide_index=True)

elif page == "المبيعات":
    st.subheader("إدخال حركة بيع")
    _, rlabels = rep_options()
    _, clabels = customer_options()
    _, plabels = product_options()
    if not clabels or not plabels:
        st.warning("يجب وجود زبون ومادة قبل إدخال المبيعات.")
    else:
        rep_label = st.selectbox("المندوب", list(rlabels.keys()))
        customer_label = st.selectbox("الزبون", list(clabels.keys()))
        product_label = st.selectbox("المادة", list(plabels.keys()))
        product = plabels[product_label]
        c1,c2,c3,c4 = st.columns(4)
        qty = c1.number_input("الكمية", min_value=0.0, step=1.0, value=1.0)
        unit_price = c2.number_input("سعر الوحدة", min_value=0.0, step=1.0, value=float(product.get("sale_price") or 0))
        discount = c3.number_input("الحسم %", min_value=0.0, max_value=100.0, step=0.5, value=0.0)
        sale_date = c4.date_input("التاريخ", value=date.today())
        liters = float(product["volume"]) * qty
        total = __import__('math').ceil(qty * unit_price * (1-discount/100))
        st.info(f"الحجم: {product['volume']:g} لتر | مجموع الليترات: {liters:g} | الإجمالي المتوقع: {money(total)}")
        if st.button("حفظ حركة البيع", type="primary"):
            if qty <= 0:
                st.error("الكمية يجب أن تكون أكبر من صفر.")
            else:
                try:
                    _, l, t = db.add_sale(rlabels[rep_label], clabels[customer_label]["id"], product["id"], qty, unit_price, discount, sale_date)
                    st.success(f"تم الحفظ — {l:g} لتر — إجمالي {money(t)}")
                    st.rerun()
                except Exception as ex:
                    st.error(str(ex))
    st.markdown("#### آخر حركات البيع")
    st.dataframe(db.recent_sales(100), use_container_width=True, hide_index=True)

elif page == "المقبوضات":
    st.subheader("إدخال مقبوض")
    _, rlabels = rep_options()
    _, clabels = customer_options()
    rep_label = st.selectbox("المندوب", list(rlabels.keys()))
    customer_label = st.selectbox("الزبون", list(clabels.keys())) if clabels else None
    c1,c2,c3 = st.columns(3)
    amount = c1.number_input("قيمة الدفعة", min_value=0.0, step=1.0)
    pdate = c2.date_input("التاريخ", value=date.today())
    fx = c3.number_input("سعر الصرف (اختياري)", min_value=0.0, step=1.0)
    if st.button("حفظ المقبوض", type="primary"):
        if not customer_label or amount <= 0:
            st.error("اختر الزبون وأدخل دفعة أكبر من صفر.")
        else:
            try:
                db.add_payment(rlabels[rep_label], clabels[customer_label]["id"], amount, pdate, fx)
                st.success("تم حفظ المقبوض.")
                st.rerun()
            except Exception as ex:
                st.error(str(ex))
    st.markdown("#### آخر المقبوضات")
    st.dataframe(db.recent_payments(100), use_container_width=True, hide_index=True)

elif page == "الزبائن":
    st.subheader("الزبائن")
    tab1, tab2 = st.tabs(["عرض وبحث", "إضافة زبون"])
    with tab1:
        search = st.text_input("بحث بالاسم أو رقم الحساب أو المنطقة")
        data = db.customers(search)
        show = [{
            "رقم الحساب":x["account_no"], "الزبون":x["name"], "المندوب":x.get("rep_code") or "", "المنطقة":x.get("region") or "",
            "التصنيف":x.get("category") or "", "المبيعات":round(x["sales_total"],2), "التحصيل":round(x["payments_total"],2),
            "التسوية":round(x["balance_adjustment"],2), "الرصيد":round(x["balance"],2)
        } for x in data]
        st.dataframe(show, use_container_width=True, hide_index=True)
    with tab2:
        _, rlabels = rep_options()
        with st.form("new_customer"):
            c1,c2 = st.columns(2)
            account = c1.text_input("رقم الحساب *")
            name = c2.text_input("اسم الزبون *")
            c3,c4,c5 = st.columns(3)
            rep_label = c3.selectbox("المندوب", ["بدون"] + list(rlabels.keys()))
            region = c4.text_input("المنطقة")
            category = c5.text_input("التصنيف")
            c6,c7 = st.columns(2)
            mobile = c6.text_input("الجوال")
            visit = c7.text_input("يوم الزيارة")
            c8,c9 = st.columns(2)
            opening = c8.number_input("رصيد افتتاحي", value=0.0)
            adj = c9.number_input("تسوية/حسم على الرصيد", value=0.0)
            submit = st.form_submit_button("إضافة الزبون", type="primary")
            if submit:
                if not account.strip() or not name.strip():
                    st.error("رقم الحساب واسم الزبون مطلوبان.")
                else:
                    try:
                        db.add_customer(account, name, None if rep_label=="بدون" else rlabels[rep_label], region, category, visit, mobile, opening, adj)
                        st.success("تمت إضافة الزبون.")
                        st.rerun()
                    except sqlite3.IntegrityError:
                        st.error("رقم الحساب موجود مسبقًا.")

elif page == "المواد والمخزون":
    st.subheader("المواد والمخزون")
    tab1, tab2 = st.tabs(["المخزون الحالي", "إضافة مادة"])
    with tab1:
        search = st.text_input("بحث باسم المادة")
        data = db.products(search)
        show = [{
            "المادة":x["name"], "الحجم":x["volume"], "رصيد افتتاحي":x["opening_stock"], "المباع وحدات":round(x["sold_qty"],2),
            "الرصيد الحالي":round(x["current_stock"],2), "ليترات مباعة":round(x["sold_liters"],2), "سعر البيع":x["sale_price"], "التكلفة":x["cost_price"]
        } for x in data]
        st.dataframe(show, use_container_width=True, hide_index=True)
    with tab2:
        with st.form("new_product"):
            name = st.text_input("اسم المادة *")
            c1,c2,c3,c4 = st.columns(4)
            volume = c1.number_input("الحجم", min_value=0.0, step=1.0)
            opening = c2.number_input("الرصيد الافتتاحي", min_value=0.0, step=1.0)
            sale_price = c3.number_input("سعر البيع", min_value=0.0, step=1.0)
            cost = c4.number_input("سعر التكلفة", min_value=0.0, step=1.0)
            submit = st.form_submit_button("إضافة المادة", type="primary")
            if submit:
                if not name.strip():
                    st.error("اسم المادة مطلوب.")
                else:
                    try:
                        db.add_product(name, volume, opening, sale_price, cost)
                        st.success("تمت إضافة المادة.")
                        st.rerun()
                    except sqlite3.IntegrityError:
                        st.error("هذه المادة موجودة بنفس الاسم والحجم.")

elif page == "عمر الدين":
    st.subheader("عمر الدين")
    as_of = st.date_input("احتساب العمر حتى", value=date.today())
    data = db.debt_age(as_of)
    c1,c2,c3,c4 = st.columns(4)
    c1.metric("إجمالي حسابات مدينة", len(data))
    c2.metric("إجمالي الدين", money(sum(float(x["الرصيد"]) for x in data)))
    c3.metric("+90 يوم", sum(1 for x in data if x["التصنيف"]=="+90 يوم"))
    c4.metric("1-30 يوم", sum(1 for x in data if x["التصنيف"]=="1-30 يوم"))
    st.dataframe(data, use_container_width=True, hide_index=True)

elif page == "المحصلة الشهرية":
    st.subheader("المحصلة الشهرية")
    data = db.monthly_summary()
    st.dataframe(data, use_container_width=True, hide_index=True)
    st.download_button("تنزيل CSV", db.csv_bytes(data), "monthly_summary.csv", "text/csv")

elif page == "خطط المندوبين":
    st.subheader("خطط ومسارات المندوبين")
    st.dataframe(db.routes(), use_container_width=True, hide_index=True)

elif page == "التدقيق والنسخ الاحتياطي":
    st.subheader("تدقيق المصدر")
    st.warning("روابط Excel الخارجية لم تُستورد كأرصدة افتتاحية تلقائيًا، لأن ذلك قد يكرر نفس حركات 2026. يمكن إدخال الأرصدة الافتتاحية لاحقًا بعد اعتماد مصدرها.")
    issues = db.audit_issues()
    st.dataframe(issues, use_container_width=True, hide_index=True)
    st.markdown("#### معلومات الاستيراد")
    st.json(meta)
    st.markdown("#### نسخة احتياطية")
    database_path = Path(db.DB_PATH)
    st.download_button("تنزيل قاعدة البيانات SQLite", database_path.read_bytes(), "total_sales_backup.db", "application/octet-stream")
    st.download_button("تنزيل الزبائن CSV", db.csv_bytes(db.customers()), "customers.csv", "text/csv")
    st.download_button("تنزيل المواد CSV", db.csv_bytes(db.products()), "products.csv", "text/csv")
