from __future__ import annotations

import csv
import io
import math
import sqlite3
from datetime import date, datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "data" / "total_sales.db"


def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def rows(sql, params=()):
    with connect() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def scalar(sql, params=(), default=0):
    with connect() as conn:
        r = conn.execute(sql, params).fetchone()
        return r[0] if r and r[0] is not None else default


def execute(sql, params=()):
    with connect() as conn:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid


def reps():
    return rows("SELECT code,name FROM reps WHERE active=1 ORDER BY CASE code WHEN 'B' THEN 1 WHEN 'C' THEN 2 WHEN 'G' THEN 3 WHEN 'E' THEN 4 WHEN 'F' THEN 5 WHEN 'A' THEN 6 ELSE 9 END,name")


def customers(search=""):
    q = """SELECT c.*,
      COALESCE((SELECT SUM(s.total) FROM sales s WHERE s.customer_id=c.id),0) AS sales_total,
      COALESCE((SELECT SUM(p.amount) FROM payments p WHERE p.customer_id=c.id),0) AS payments_total,
      c.opening_balance + COALESCE((SELECT SUM(s.total) FROM sales s WHERE s.customer_id=c.id),0)
      - COALESCE((SELECT SUM(p.amount) FROM payments p WHERE p.customer_id=c.id),0) - c.balance_adjustment AS balance
      FROM customers c"""
    params = []
    if search:
        q += " WHERE c.account_no LIKE ? OR c.name LIKE ? OR c.region LIKE ?"
        s = f"%{search}%"
        params = [s, s, s]
    q += " ORDER BY c.name"
    return rows(q, params)


def products(search=""):
    q = """SELECT p.*,
      COALESCE((SELECT SUM(s.quantity) FROM sales s WHERE s.product_id=p.id),0) AS sold_qty,
      p.opening_stock - COALESCE((SELECT SUM(s.quantity) FROM sales s WHERE s.product_id=p.id),0) AS current_stock,
      COALESCE((SELECT SUM(s.liters) FROM sales s WHERE s.product_id=p.id),0) AS sold_liters,
      COALESCE((SELECT SUM(s.total) FROM sales s WHERE s.product_id=p.id),0) AS sales_value
      FROM products p"""
    params = []
    if search:
        q += " WHERE p.name LIKE ?"
        params = [f"%{search}%"]
    q += " ORDER BY p.name,p.volume"
    return rows(q, params)


def add_customer(account_no, name, rep_code=None, region="", category="", visit_day="", mobile="", opening_balance=0.0, adjustment=0.0):
    return execute("""INSERT INTO customers(account_no,rep_code,name,region,category,visit_day,mobile,opening_balance,balance_adjustment)
                    VALUES(?,?,?,?,?,?,?,?,?)""",
                   (account_no.strip(), rep_code or None, name.strip(), region.strip(), category.strip(), visit_day.strip(), mobile.strip(), float(opening_balance), float(adjustment)))


def add_product(name, volume, opening_stock, sale_price, cost_price):
    return execute("INSERT INTO products(name,volume,opening_stock,sale_price,cost_price) VALUES(?,?,?,?,?)",
                   (name.strip(), float(volume), float(opening_stock), float(sale_price), float(cost_price)))


def add_sale(rep_code, customer_id, product_id, quantity, unit_price, discount_percent, sale_date):
    with connect() as conn:
        c = conn.execute("SELECT account_no FROM customers WHERE id=?", (customer_id,)).fetchone()
        p = conn.execute("SELECT name,volume FROM products WHERE id=?", (product_id,)).fetchone()
        if not c or not p:
            raise ValueError("الزبون أو المادة غير موجودة")
        qty = float(quantity)
        volume = float(p["volume"] or 0)
        discount_rate = float(discount_percent or 0) / 100.0
        liters = volume * qty
        total = math.ceil(qty * float(unit_price) * (1 - discount_rate))
        dt = sale_date.isoformat() if hasattr(sale_date, "isoformat") else str(sale_date)
        cur = conn.execute("""INSERT INTO sales(rep_code,customer_id,account_no,product_id,product_name,volume,quantity,liters,unit_price,discount_rate,total,sale_date,month)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (rep_code, customer_id, c["account_no"], product_id, p["name"], volume, qty, liters, float(unit_price), discount_rate, total, dt, int(dt[5:7])))
        conn.commit()
        return cur.lastrowid, liters, total


def add_payment(rep_code, customer_id, amount, payment_date, fx_rate=0.0):
    with connect() as conn:
        c = conn.execute("SELECT account_no FROM customers WHERE id=?", (customer_id,)).fetchone()
        if not c:
            raise ValueError("الزبون غير موجود")
        dt = payment_date.isoformat() if hasattr(payment_date, "isoformat") else str(payment_date)
        cur = conn.execute("INSERT INTO payments(rep_code,customer_id,account_no,amount,payment_date,month,fx_rate) VALUES(?,?,?,?,?,?,?)",
                           (rep_code, customer_id, c["account_no"], float(amount), dt, int(dt[5:7]), float(fx_rate or 0)))
        conn.commit()
        return cur.lastrowid


def dashboard(start_date=None, end_date=None, rep_code=None):
    clauses = ["1=1"]
    params = []
    if start_date:
        clauses.append("sale_date>=?")
        params.append(str(start_date))
    if end_date:
        clauses.append("sale_date<=?")
        params.append(str(end_date))
    if rep_code:
        clauses.append("rep_code=?")
        params.append(rep_code)
    where = " AND ".join(clauses)
    with connect() as conn:
        sale = conn.execute(f"SELECT COUNT(*),COALESCE(SUM(liters),0),COALESCE(SUM(total),0) FROM sales WHERE {where}", params).fetchone()
        # Payment date uses its own where.
        pcl = ["1=1"]
        pparams = []
        if start_date:
            pcl.append("payment_date>=?"); pparams.append(str(start_date))
        if end_date:
            pcl.append("payment_date<=?"); pparams.append(str(end_date))
        if rep_code:
            pcl.append("rep_code=?"); pparams.append(rep_code)
        pay = conn.execute(f"SELECT COALESCE(SUM(amount),0) FROM payments WHERE {' AND '.join(pcl)}", pparams).fetchone()[0]
        balance = conn.execute("""SELECT COALESCE(SUM(c.opening_balance + COALESCE(s.st,0) - COALESCE(p.pt,0) - c.balance_adjustment),0)
          FROM customers c
          LEFT JOIN (SELECT customer_id,SUM(total) st FROM sales GROUP BY customer_id) s ON s.customer_id=c.id
          LEFT JOIN (SELECT customer_id,SUM(amount) pt FROM payments GROUP BY customer_id) p ON p.customer_id=c.id""").fetchone()[0]
        return {"sales_count": sale[0], "liters": sale[1], "sales_value": sale[2], "payments": pay, "balance": balance}


def recent_sales(limit=100):
    return rows("""SELECT s.id,s.sale_date,r.name AS rep,c.account_no,c.name AS customer,s.product_name,s.volume,s.quantity,s.liters,s.unit_price,s.total
        FROM sales s LEFT JOIN reps r ON r.code=s.rep_code LEFT JOIN customers c ON c.id=s.customer_id
        ORDER BY s.sale_date DESC,s.id DESC LIMIT ?""", (limit,))


def recent_payments(limit=100):
    return rows("""SELECT p.id,p.payment_date,r.name AS rep,c.account_no,c.name AS customer,p.amount,p.fx_rate
        FROM payments p LEFT JOIN reps r ON r.code=p.rep_code LEFT JOIN customers c ON c.id=p.customer_id
        ORDER BY p.payment_date DESC,p.id DESC LIMIT ?""", (limit,))


def monthly_summary():
    sales = rows("""SELECT month,rep_code,COUNT(*) tx,ROUND(SUM(liters),3) liters,ROUND(SUM(total),3) sales_value
                    FROM sales GROUP BY month,rep_code""")
    payments = rows("SELECT month,rep_code,ROUND(SUM(amount),3) payments FROM payments GROUP BY month,rep_code")
    paymap = {(r["month"], r["rep_code"]): r["payments"] for r in payments}
    repmap = {r["code"]: r["name"] for r in reps()}
    out = []
    for r in sales:
        p = paymap.get((r["month"], r["rep_code"]), 0)
        out.append({"الشهر": r["month"], "المندوب": repmap.get(r["rep_code"], r["rep_code"]), "الليترات": r["liters"],
                    "المبيعات": r["sales_value"], "التحصيل": p, "فرق التحصيل-المبيعات": round(p-r["sales_value"], 3)})
    for r in payments:
        key = (r["month"], r["rep_code"])
        if not any(x["الشهر"] == r["month"] and x["المندوب"] == repmap.get(r["rep_code"], r["rep_code"]) for x in out):
            out.append({"الشهر": r["month"], "المندوب": repmap.get(r["rep_code"], r["rep_code"]), "الليترات": 0,
                        "المبيعات": 0, "التحصيل": r["payments"], "فرق التحصيل-المبيعات": r["payments"]})
    return sorted(out, key=lambda x: (x["الشهر"], x["المندوب"]))


def debt_age(as_of: date):
    base = customers()
    last_pay = {r["customer_id"]: r["last_payment"] for r in rows("SELECT customer_id,MAX(payment_date) last_payment FROM payments GROUP BY customer_id")}
    first_sale = {r["customer_id"]: r["first_sale"] for r in rows("SELECT customer_id,MIN(sale_date) first_sale FROM sales GROUP BY customer_id")}
    out = []
    for c in base:
        bal = float(c["balance"] or 0)
        if bal <= 0:
            continue
        lp = last_pay.get(c["id"])
        anchor = lp or first_sale.get(c["id"])
        if anchor:
            try:
                days = (as_of - datetime.strptime(anchor, "%Y-%m-%d").date()).days
            except Exception:
                days = None
        else:
            days = None
        if days is None:
            bucket = "لا يوجد تاريخ"
        elif days <= 30:
            bucket = "1-30 يوم"
        elif days <= 60:
            bucket = "31-60 يوم"
        elif days <= 90:
            bucket = "61-90 يوم"
        else:
            bucket = "+90 يوم"
        out.append({"المندوب": c.get("rep_code") or "", "رقم الحساب": c["account_no"], "اسم الزبون": c["name"], "المنطقة": c.get("region") or "",
                    "آخر دفعة": lp or "—", "الرصيد": round(bal, 3), "عدد الأيام": days if days is not None else "—", "التصنيف": bucket})
    return sorted(out, key=lambda x: (999999 if x["عدد الأيام"] == "—" else -int(x["عدد الأيام"])))


def audit_issues():
    return rows("SELECT severity,category,location,details FROM audit_issues ORDER BY CASE severity WHEN 'critical' THEN 1 WHEN 'warning' THEN 2 ELSE 3 END,id")


def meta():
    return {r["key"]: r["value"] for r in rows("SELECT key,value FROM meta")}


def routes():
    return rows("""SELECT rr.day_name AS day,r.name AS rep,rr.route_text AS route FROM rep_routes rr
                   LEFT JOIN reps r ON r.code=rr.rep_code ORDER BY rr.rep_code,rr.id""")


def csv_bytes(data):
    if not data:
        return b""
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=list(data[0].keys()))
    writer.writeheader()
    writer.writerows(data)
    return output.getvalue().encode("utf-8-sig")
