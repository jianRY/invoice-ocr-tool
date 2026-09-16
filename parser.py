#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""发票图片 OCR 解析核心：RapidOCR + 坐标化字段提取。

支持：数电票/增值税电子普通发票、全国通用电子统一票据、
财政医疗收费票据明细页（无主页时按页解析）。

parse_image(eng, path) -> dict:
    file, invoice_no, date, buyer, seller,
    items[{name, qty, amount}], total, ok, reason
"""
import os
import re

IMG_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}

_num = r"[\d,]+(?:\.\d+)?"
COL_TOL = 175  # 列头与数值 token 的横向匹配容差(px)


def _center(item):
    box, text = item[0], item[1]
    xs = [p[0] for p in box]
    ys = [p[1] for p in box]
    return (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, min(xs), min(ys), text


def to_f32(x):
    try:
        return float(str(x).replace(",", "").replace(" ", ""))
    except (TypeError, ValueError):
        return None


def _full_text(lines):
    return " ".join(t for *_, t in lines)


def parse_image(eng, path):
    rec = {"file": os.path.basename(path), "invoice_no": "", "date": "",
           "buyer": "", "seller": "", "items": [], "total": None,
           "ok": False, "reason": ""}
    try:
        result, _ = eng(path)
    except Exception as e:
        rec["reason"] = f"OCR 失败: {e}"
        return rec
    if not result:
        rec["reason"] = "未检出文字"
        return rec
    lines = [_center(it) for it in result]
    W = max(max(p[0] for p in it[0]) for it in result)
    full = _full_text(lines)

    rec["invoice_no"] = _find_invoice_no(full, lines, W)
    rec["date"] = _find_date(full)
    rec["buyer"], rec["seller"] = _find_parties(lines, W)
    rec["total"] = _find_total(lines)
    rec["items"] = _find_items(lines, W)

    if not rec["invoice_no"]:
        rec["reason"] = "未识别出发票号码"
    elif rec["total"] is None:
        rec["reason"] = "未识别出价税合计"
    elif not rec["items"]:
        rec["reason"] = "未识别出明细项目"
    else:
        rec["ok"] = True
    return rec


def _find_invoice_no(full, lines, W):
    m = re.search(r"发票号码[：:]\s*(\d{8,30})", full)
    if m:
        return m.group(1)
    m = re.search(r"票据号码[：:]\s*(\d{6,30})", full)
    if m:
        return m.group(1)
    for cx, cy, x0, y0, t in lines:
        m = re.match(r"^(\d{20})$", t.strip())
        if m and cx > W * 0.55 and cy < 400:
            return m.group(1)
    return ""


def _find_date(full):
    m = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", full)
    if m:
        y, mo, d = m.groups()
        return f"{y}-{int(mo):02d}-{int(d):02d}"
    m = re.search(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", full)
    if m:
        y, mo, d = m.groups()
        if 1 <= int(mo) <= 12 and 1 <= int(d) <= 31:
            return f"{y}-{int(mo):02d}-{int(d):02d}"
    return ""


def _name_value(t):
    return re.sub(r"^[^：:]{0,8}[：:]", "", t).strip()


def _find_parties(lines, W):
    """优先购买方/销售方标签定位；否则按名称行的上/下布局推断。"""
    name_rows = [(cx, cy, t) for cx, cy, x0, y0, t in lines
                 if re.search(r"(名称|称)\s*[：:]", t)
                 and not re.search(r"开户|账号|地址|电话|信用代码|识别号|银行", t)]
    buyer = seller = ""
    # 财政票据：交款人：xxx 直接带值
    for cx, cy, x0, y0, t in lines:
        m = re.search(r"交款人[：:]\s*(\S+)", t)
        if m and not buyer:
            buyer = m.group(1)
    b_lab = next(((cx, cy) for cx, cy, x0, y0, t in lines
                  if re.search(r"购买方", t) and "交款人" not in t), None)
    s_lab = next(((cx, cy) for cx, cy, x0, y0, t in lines
                  if re.search(r"销售方信息|销售方名称", t)), None)
    if b_lab:
        below = [(cy, t) for cx, cy, t in name_rows
                 if b_lab[1] - 20 < cy < b_lab[1] + 200]
        if below:
            buyer = _name_value(min(below)[1])
    if s_lab:
        below = [(cy, t) for cx, cy, t in name_rows
                 if s_lab[1] - 20 < cy < s_lab[1] + 200]
        if below:
            seller = _name_value(min(below)[1])
    if not (buyer and seller) and len(name_rows) >= 2:
        rows = sorted(name_rows, key=lambda r: (r[1], r[0]))[:2]
        (_, cy1, t1), (_, cy2, t2) = rows
        buyer, seller = _name_value(t1), _name_value(t2)
    elif not buyer and name_rows:
        buyer = _name_value(name_rows[0][2])
    return buyer, seller


def _find_total(lines):
    """优先（小写）￥；其次全部 ￥ 取最大；再次合计行数值。"""
    full = _full_text(lines)
    m = re.search(r"[（(]小写[)）]\s*[￥¥?]?\s*(" + _num + r")", full)
    if m:
        return to_f32(m.group(1))
    cands = []
    for cx, cy, x0, y0, t in lines:
        for mm in re.finditer(r"[￥¥]\s*(" + _num + r")", t):
            v = to_f32(mm.group(1))
            if v:
                cands.append(v)
    if cands:
        return max(cands)
    for i, (cx, cy, x0, y0, t) in enumerate(lines):
        if re.search(r"合\s*计|金额合计", t):
            for j, (cx2, cy2, x02, y02, t2) in enumerate(lines):
                if j == i or abs(cy2 - cy) >= 22:
                    continue
                tt = re.sub(r"\s+", "", t2)
                v = to_f32(tt.lstrip("￥¥"))
                if v and re.fullmatch(r"[￥¥]?" + _num, tt):
                    return v
    return None


def _find_items(lines, W):
    # 列头（宽松匹配：token 含关键词即可，排除合计行干扰）
    headers = {}
    for cx, cy, x0, y0, t in lines:
        if "项目名称" in t or "货物或服务名称" in t or "收费项目" in t:
            headers.setdefault("name", (cx, cy))
        elif re.search(r"数量", t):
            headers.setdefault("qty", (cx, cy))
        elif re.search(r"单价", t):
            headers.setdefault("price", (cx, cy))
        elif re.search(r"金额", t) and not re.search(r"价税|大写|小写|合计", t):
            headers.setdefault("amount", (cx, cy))
        elif re.search(r"税额", t):
            headers.setdefault("taxamt", (cx, cy))
    if "name" not in headers:
        return []
    name_y = headers["name"][1]
    end_y = None
    for cx, cy, x0, y0, t in lines:
        if re.search(r"价税合计|金额合计|合\s*计|小\s*计", t) and cy > name_y:
            end_y = cy if end_y is None else min(end_y, cy)
    if end_y is None:
        end_y = max(cy for cx, cy, x0, y0, t in lines)
    region = [ln for ln in lines if name_y + 8 < ln[1] < end_y - 8]
    if not region:
        return []

    region.sort(key=lambda ln: (ln[1], ln[0]))
    rows = []
    for ln in region:
        if rows and abs(ln[1] - rows[-1][0][1]) < 22:
            rows[-1].append(ln)
        else:
            rows.append([ln])

    amount_cx = headers.get("amount", (None,))[0]
    qty_cx = headers.get("qty", (None,))[0]

    def claim(row, col_cx, exclude):
        best, best_d = None, 10 ** 9
        for cx, cy, x0, y0, t in row:
            if (cx, cy, t) in exclude:
                continue
            tt = re.sub(r"\s+", "", t)
            m = re.fullmatch(r"[￥¥]?(" + _num + r")(?:/\S+)?", tt)
            if not m:
                continue
            d = abs(cx - col_cx)
            if d < COL_TOL and d < best_d:
                best, best_d = to_f32(m.group(1)), d
        return best

    items = []
    for row in rows:
        name_toks = [(cx, cy, t) for cx, cy, x0, y0, t in row
                     if re.search(r"[\u4e00-\u9fff*]", t)]
        name = ""
        if name_toks:
            name = max((t for *_r, t in name_toks), key=len)
        amount = None
        if amount_cx is not None:
            amount = claim(row, amount_cx, set())
        # 分类小节行（如"诊察费："结尾冒号且无数量）→ 跳过
        has_qty_tok = any(re.search(r"\d", t) and qty_cx and abs(cx - qty_cx) < COL_TOL
                          for cx, cy, x0, y0, t in row)
        if name.endswith(("：", ":")) and not has_qty_tok:
            continue
        if amount is None and not name:
            continue
        if amount is None:
            items.append({"name": name, "qty": "", "amount": None})
            continue
        qty = ""
        if qty_cx is not None:
            q = claim(row, qty_cx, set())
            if q is not None:
                for cx, cy, x0, y0, t in row:
                    tt = re.sub(r"\s+", "", t)
                    m = re.fullmatch(r"(" + _num + r")(?:/\S+)?", tt)
                    if m and to_f32(m.group(1)) == q:
                        qty = m.group(1)
                        break
        items.append({"name": name, "qty": qty, "amount": amount})

    merged, pending = [], ""
    for it in items:
        if it["amount"] is None:
            pending += it["name"]
        else:
            if pending:
                it["name"] = pending + it["name"]
                pending = ""
            merged.append({"name": it["name"], "qty": it["qty"],
                           "amount": it["amount"]})
    if pending and merged:
        merged[-1]["name"] += pending
    return merged
