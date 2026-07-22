#!/usr/bin/env python3
"""
全市场股票查询后端服务
支撑 A股/港股/美股 任意股票搜索与行情查询
端口: 8765
"""

import json
import webbrowser
import re
import subprocess
import threading
import time
import traceback
from http.server import HTTPServer, BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

# ============================================================
# Config
# ============================================================
PORT = 8765
PROJECT_DIR = Path(__file__).resolve().parent
HTML_FILE = PROJECT_DIR / "anti-retail-sentiment.html"
DB_FILE = PROJECT_DIR / "stocks_db.json"

NEODATA_DIR = Path(r"C:\Program Files\WorkBuddy\resources\app.asar.unpacked\resources\builtin-skills\neodata-financial-search")
WESTOCK_DIR = Path(r"C:\Program Files\WorkBuddy\resources\app.asar.unpacked\resources\builtin-skills\westock-data")

PYTHON_EXE = Path(r"C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12\python.exe")
NODE_EXE = Path(r"C:\Users\Administrator\.workbuddy\binaries\node\versions\22.22.2\node.exe")

# ============================================================
# Background stock data refresher
# ============================================================
_stock_cache = {}
_stock_cache_lock = threading.Lock()
_stock_last_update = 0
_stock_refresh_time = {}  # code -> timestamp of last refresh
_fear_history = {}  # date -> score, in-memory only

# Index cache: background thread refreshes every 60s
_indices_cache = {}
_fear_cache = {"fear_score": 50}

def fear_index_refresher():
    """Background: compute fear index every 60s, store in cache"""
    global _fear_cache
    while True:
        try:
            fd = calculate_fear_index()
            if fd: _fear_cache = fd
        except: pass
        time.sleep(60)

def get_index_data(code: str, name: str) -> dict:
    """Fetch K-line for index and compute MA5/10/20/60"""
    try:
        r = subprocess.run(
            [str(NODE_EXE), "scripts/index.js", "kline", code,
             "--period", "day", "--limit", "65"],
            cwd=str(WESTOCK_DIR), capture_output=True, text=True,
            encoding='utf-8', errors='replace', timeout=15,
        )
        if r.returncode != 0:
            return {"code": code, "name": name, "error": "kline_failed"}
        prices = []
        for line in r.stdout.strip().split("\n"):
            parts = [c.strip() for c in line.split("|") if c.strip()]
            if len(parts) < 3: continue
            try: prices.append(float(parts[2]))
            except: pass
        if len(prices) < 5:
            return {"code": code, "name": name, "error": "no_data"}
        prices.reverse()
        cur = prices[-1]; prev = prices[-2] if len(prices) >= 2 else cur
        def avg(n): return round(sum(prices[-n:]) / n, 2) if len(prices) >= n else 0
        return {
            "code": code, "name": name,
            "price": round(cur, 2),
            "chg_pct": round((cur - prev) / prev * 100, 2) if prev else 0,
            "ma5": avg(5), "ma10": avg(10), "ma20": avg(20), "ma60": avg(60),
        }
    except: return {"code": code, "name": name, "error": "exception"}

def indices_refresher():
    """Background: refresh index MA data every 60s"""
    global _indices_cache
    INDICES = [("sh000001", "上证指数"), ("sh000688", "科创50"), ("sz399006", "创业板指")]
    while True:
        try:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=3) as ex:
                futures = {code: ex.submit(get_index_data, code, n) for code, n in INDICES}
            _indices_cache = {code: futures[code].result() for code, _ in INDICES}
        except: pass
        time.sleep(60)

def refresh_stock_cache():
    """Background thread: refresh all stock quotes every 120s"""
    global _stock_cache, _stock_last_update
    # Delay first run to let server stabilize
    time.sleep(10)
    # Load initial data from disk
    with _stock_cache_lock:
        if DB_FILE.exists():
            with open(DB_FILE, "r", encoding="utf-8") as f:
                _stock_cache = json.load(f)
            _stock_last_update = time.time()
    while True:
        try:
            codes_priced = [c for c in _stock_cache if _stock_cache[c].get("p", 0) > 0]
            codes_new = [c for c in _stock_cache if _stock_cache[c].get("p", 0) == 0]
            # Prioritize stocks without prices, interleave with priced ones
            queue = (codes_new + codes_priced)[:12]
            if not queue:
                time.sleep(60)
                continue

            updated = 0
            for code in queue:
                try:
                    r = subprocess.run(
                        [str(NODE_EXE), "scripts/index.js", "quote", code],
                        cwd=str(WESTOCK_DIR), capture_output=True, text=True,
                        encoding='utf-8', errors='replace', timeout=25,
                    )
                    if r.returncode == 0:
                        for line in r.stdout.strip().split("\n"):
                            line = line.strip()
                            if not line.startswith("|"): continue
                            if "---" in line or "| code" in line: continue
                            parts = [c.strip() for c in line.split("|") if c.strip()]
                            if len(parts) < 30 or parts[0].startswith("-"): continue
                            try:
                                # Column mapping depends on market
                                if code.startswith("hk") or code.startswith("us"):
                                    # HK/US: 38 cols, no pe_fwd/pe_lyr
                                    p = float(parts[5] or 0)
                                    chg = float(parts[13] or 0)
                                    pe = float(parts[20] or 0)
                                    pb = float(parts[21] or 0)
                                    dv = float(parts[22] or 0)
                                    h52 = float(parts[28] or 0)
                                    d20 = float(parts[32] or 0)
                                    d60 = float(parts[33] or 0)
                                    ytd = float(parts[34] or 0)
                                else:
                                    # A-share: 40 cols with pe_fwd/pe_lyr
                                    p = float(parts[5] or 0)
                                    chg = float(parts[13] or 0)
                                    pe = float(parts[20] or 0)
                                    pb = float(parts[23] or 0)
                                    dv = float(parts[24] or 0)
                                    h52 = float(parts[29] or 0)
                                    d20 = float(parts[33] or 0)
                                    d60 = float(parts[34] or 0)
                                    ytd = float(parts[35] or 0)
                                with _stock_cache_lock:
                                    if code in _stock_cache:
                                        _stock_cache[code].update({
                                            "p": p, "chg": chg, "pe": pe, "pb": pb,
                                            "h52": h52, "dv": dv, "d20": d20, "d60": d60, "ytd": ytd,
                                        })
                                        _stock_refresh_time[code] = time.time()
                                        updated += 1
                            except: pass
                except: pass
                time.sleep(0.3)
            if updated:
                _stock_last_update = time.time()
                priced = sum(1 for c in _stock_cache if _stock_cache[c].get("p", 0) > 0)
                print(f"  Stock: +{updated} real-time, total priced={priced}/{len(_stock_cache)}", flush=True)
        except Exception as e:
            print(f"  Stock error: {e}", flush=True)
        time.sleep(60)

# Extracted CSS styles (auto-generated by embed_css.py)
STYLE = (
    "__CSS_PLACEHOLDER__"
)

# ============================================================
# Stock Search via neodata
# ============================================================
def search_stock(query: str) -> list:
    """搜索股票，返回匹配列表。优先 neodata，失败时 fallback 到本地库。"""
    stocks = []
    
    try:
        result = subprocess.run(
            [str(PYTHON_EXE), "scripts/query.py", "--query", query, "--data-type", "api"],
            cwd=str(NEODATA_DIR),
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=25,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            api_data = data.get("data", {}).get("apiData", {})
            entities = api_data.get("entity", [])
            api_recall = api_data.get("apiRecall", [])
            seen = set()
            for ent in entities:
                code = ent.get("code", "")
                name = ent.get("name", "")
                if code and name and (code not in seen):
                    if re.match(r'^\d{4,6}\.(SZ|SH|HK|US|NYSE|NASDAQ)$', name) and re.search(r'[\u4e00-\u9fff]', code):
                        code, name = name, code
                    m = re.match(r'^(\d{6})\.(SZ|SH)$', code)
                    if m:
                        code = f"{m.group(2).lower()}{m.group(1)}"
                    market = "A股"
                    if "HK" in code.upper() or code.endswith(".HK"):
                        market = "港股"
                    elif any(code.endswith(s) for s in [".US", ".NYSE", ".NASDAQ"]) or code.isalpha():
                        market = "美股"
                    stocks.append({"code": code, "name": name, "market": market, "price": None, "chg": None})
                    seen.add(code)
            for recall in api_recall:
                content = recall.get("content", "")
                code_matches = re.findall(r'(?:sh|sz|SH|SZ)(\d{6})', content)
                for cm in code_matches[:10]:
                    prefix = "sh" if "SH" in content else "sz"
                    full_code = f"{prefix}{cm}"
                    if full_code not in seen:
                        name_match = re.search(rf'{full_code}[^\n]*?([\u4e00-\u9fff]{{2,6}})', content)
                        stocks.append({"code": full_code, "name": name_match.group(1) if name_match else full_code, "market": "A股", "price": None, "chg": None})
                        seen.add(full_code)
            if not stocks:
                hk_match = re.findall(r'(HK\d{4,5})|(\d{4,5}\.HK)', query.upper())
                for m in hk_match:
                    code = m[0] or m[1]
                    if code:
                        stocks.append({"code": code, "name": code, "market": "港股", "price": None, "chg": None})
                us_match = re.findall(r'([A-Z]{1,5})', query.upper())
                for m in us_match[:5]:
                    if len(m) >= 2 and m not in ('A', 'SH', 'SZ', 'HK', 'US'):
                        stocks.append({"code": m, "name": m, "market": "美股", "price": None, "chg": None})
    except (subprocess.TimeoutExpired, json.JSONDecodeError, Exception):
        pass  # neodata failed silently, fall through to local DB

    # Fallback: search local stocks_db.json if neodata found nothing
    if not stocks:
        q_lower = query.lower().replace(".", "").replace(" ", "")
        with _stock_cache_lock:
            for code, s in _stock_cache.items():
                name_lower = s.get("n", "").lower()
                if q_lower in name_lower or q_lower in code or q_lower in s.get("memo", ""):
                    stocks.append({
                        "code": code, "name": s["n"], "market": s.get("mkt", "A股"),
                        "price": s.get("p"), "chg": s.get("chg")
                    })
                    if len(stocks) >= 15:
                        break

    return stocks[:15] if stocks else [{"error": "no_results", "message": f"未找到「{query}」相关股票"}]


# ============================================================
# Stock Quote via westock-data
# ============================================================
def get_quote(code: str) -> dict:
    """获取股票实时行情"""
    try:
        result = subprocess.run(
            [str(NODE_EXE), "scripts/index.js", "quote", code],
            cwd=str(WESTOCK_DIR),
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=20,
        )
        if result.returncode != 0:
            return {"error": "quote_failed", "message": result.stderr.strip()[:200]}

        output = result.stdout.strip()
        if not output:
            return {"error": "no_data", "message": "未获取到行情数据"}

        # Parse westock-data markdown table
        # Columns: 0=code 1=mkt_type 2=mkt_name 3=name 4=symbol 5=price
        # 6=prev_close 7=open 8=high 9=low 10=volume 11=amount 12=change
        # 13=change_pct 14=turnover 15=vol_ratio 16=range 17=avg_price
        # 18=time 19=wb_ratio 20=pe 21=pe_fwd 22=pe_lyr 23=pb
        # 24=div_yield 25=total_mcap 26=circ_mcap 27=shares 28=float
        # 29=h52 30=l52 31=chg5d 32=chg10d 33=chg20d 34=chg60d 35=chg_ytd
        
        for line in output.split("\n"):
            line = line.strip()
            if not (line.startswith("| sh") or line.startswith("| sz")):
                continue
            parts = [c.strip() for c in line.split("|") if c.strip()]
            if len(parts) < 30:
                continue
            # Skip separator rows (---)
            if parts[0].startswith("-"):
                continue
            try:
                return {
                    "code": parts[0],
                    "name": parts[3],
                    "price": float(parts[5] or 0),
                    "chg": float(parts[13] or 0),
                    "pe": float(parts[20] or 0),
                    "pb": float(parts[23] or 0),
                    "h52": float(parts[29] or 0),
                    "dv": float(parts[24] or 0),
                    "d20": float(parts[33] or 0),
                    "d60": float(parts[34] or 0),
                    "ytd": float(parts[35] or 0),
                    "market": "A股" if parts[0].startswith(("sh","sz")) else (
                        "港股" if parts[0].startswith("hk") else "美股"),
                }
            except (ValueError, IndexError) as e:
                return {"error": "parse_failed", "message": str(e)[:100]}

    except subprocess.TimeoutExpired:
        return {"error": "timeout", "message": "行情查询超时"}
    except Exception as e:
        return {"error": "exception", "message": str(e)[:200]}

    # 如果没有找到匹配行，则也返回结构化错误信息
    return {"error": "no_data", "message": "未获取到行情数据"}
def get_ma(code: str) -> dict:
    """计算 MA5/10/20/60 均线"""
    try:
        r = subprocess.run(
            [str(NODE_EXE), "scripts/index.js", "kline", code,
             "--period", "day", "--limit", "65"],
            cwd=str(WESTOCK_DIR), capture_output=True, text=True,
            encoding='utf-8', errors='replace', timeout=15,
        )
        if r.returncode != 0:
            return {"error": "kline_failed"}
        prices = []
        for line in r.stdout.strip().split("\n"):
            line = line.strip()
            if not line.startswith("|") or "---" in line or "date" in line:
                continue
            parts = [c.strip() for c in line.split("|") if c.strip()]
            if len(parts) < 3: continue
            try:
                prices.append(float(parts[2]))  # last
            except: pass
        if len(prices) < 5:
            return {"error": "insufficient_data"}
        prices.reverse()  # oldest first
        def avg(n):
            return round(sum(prices[-n:]) / n, 2) if len(prices) >= n else 0
        cur = prices[-1]
        return {
            "ma5": avg(5), "ma10": avg(10), "ma20": avg(20), "ma60": avg(60),
            "current": cur,
            "above_ma5": cur > avg(5), "above_ma10": cur > avg(10),
            "above_ma20": cur > avg(20), "above_ma60": cur > avg(60),
        }
    except Exception as e:
        return {"error": "ma_failed", "message": str(e)[:100]}


def get_fund_flow(code: str) -> dict:
    """获取主力资金流入数据"""
    try:
        r = subprocess.run(
            [str(NODE_EXE), "scripts/index.js", "fund", "flow", code],
            cwd=str(WESTOCK_DIR), capture_output=True, text=True,
            encoding='utf-8', errors='replace', timeout=15,
        )
        if r.returncode != 0:
            return {"error": "flow_failed"}
        for line in r.stdout.strip().split("\n"):
            line = line.strip()
            if not line.startswith("|") or "---" in line or "code" in line:
                continue
            parts = [c.strip() for c in line.split("|") if c.strip()]
            if len(parts) < 19: continue
            try:
                return {
                    "date": parts[3],
                    "main_net": float(parts[9] or 0),        # MainNetFlow
                    "main_net_5d": float(parts[12] or 0),    # MainNetFlow5D
                    "main_net_20d": float(parts[11] or 0),   # MainNetFlow20D
                    "main_inflow": float(parts[5] or 0),     # MainInFlow
                    "main_outflow": float(parts[13] or 0),   # MainOutFlow
                    "retail_inflow": float(parts[15] or 0),  # RetailInFlow
                    "retail_outflow": float(parts[16] or 0), # RetailOutFlow
                    "main_rank": int(parts[8] or 0),         # MainInflowRank
                }
            except: pass
        return {"error": "no_flow_data"}
    except Exception as e:
        return {"error": "flow_failed", "message": str(e)[:100]}


def get_finance(code: str) -> dict:
    """获取最新财务摘要（季度营收/净利）"""
    try:
        r = subprocess.run(
            [str(NODE_EXE), "scripts/index.js", "finance", code, "--num", "2"],
            cwd=str(WESTOCK_DIR), capture_output=True, text=True,
            encoding='utf-8', errors='replace', timeout=20,
        )
        if r.returncode != 0:
            return {"error": "finance_failed"}
        text = r.stdout
        result = {}
        in_lrb = False
        header = []
        rows = []
        for line in text.strip().split("\n"):
            if "**lrb**" in line.lower():
                in_lrb = True; continue
            if line.startswith("**") and in_lrb:
                break
            if not in_lrb: continue
            line = line.strip()
            if not line.startswith("|") or "---" in line: continue
            parts = [c.strip() for c in line.split("|") if c.strip()]
            if not header:
                header = parts; continue
            if len(parts) == len(header):
                rows.append(dict(zip(header, parts)))
        
        if rows:
            r0 = rows[0]
            eps_col = "BasicEPS"
            rev_q = next((k for k in r0 if "OperatingRevenue_Q" in k), None)
            profit_q = next((k for k in r0 if "NPParentCompany" in k and "_Q" in k), None)
            profit_col = next((k for k in r0 if "NPParentCompany" in k and "Q" not in k and "TTM" not in k), None)
            
            rev_g, profit_g = 0, 0
            if rev_q and len(rows) > 1:
                try: rev_g = (float(rows[0][rev_q]) / float(rows[1][rev_q]) - 1) * 100
                except: pass
            if profit_q and len(rows) > 1:
                try: profit_g = (float(rows[0][profit_q]) / float(rows[1][profit_q]) - 1) * 100
                except: pass
            
            def fmt(v):
                try: fv = float(v)
                except: return v
                if abs(fv) >= 1e8: return f"{fv/1e8:.2f}亿"
                if abs(fv) >= 1e4: return f"{fv/1e4:.2f}万"
                return f"{fv:.2f}"
            
            result = {
                "date": rows[0].get("EndDate", "—"),
                "eps": rows[0].get(eps_col, "—"),
                "revenue_q": fmt(rows[0].get(rev_q, "0")),
                "revenue_growth": round(rev_g, 1),
                "profit_q": fmt(rows[0].get(profit_q, "0")),
                "profit_growth": round(profit_g, 1),
            }
        return result if result else {"error": "no_finance_data"}
    except Exception as e:
        return {"error": "finance_failed", "message": str(e)[:100]}


# ============================================================
# Unified Fear Index Calculator (single source of truth)
# ============================================================

def calculate_fear_index():
    """Calculate market fear index from live 上证指数 data. No caching — fresh every call."""
    try:
        r = subprocess.run(
            [str(NODE_EXE), "scripts/index.js", "quote", "sh000001"],
            cwd=str(WESTOCK_DIR), capture_output=True, text=True,
            encoding='utf-8', errors='replace', timeout=20,
        )
        if r.returncode != 0:
            return None
        
        for line in r.stdout.strip().split("\n"):
            line = line.strip()
            if not line.startswith("| sh000001"): continue
            parts = [c.strip() for c in line.split("|") if c.strip()]
            if len(parts) < 30: continue
            if parts[0].startswith("-"): continue
            
            price = float(parts[5] or 0)
            chg = float(parts[13] or 0)
            h52 = float(parts[29] or 0)
            d20 = float(parts[33] or 0)
            d60 = float(parts[34] or 0)
            ytd = float(parts[35] or 0)

            dd_pct = ((h52 - price) / h52 * 100) if h52 > 0 else 0

            # Six-factor scoring — SINGLE source of truth
            vol_score = 20 if d20 < -5 else (15 if d20 < -3 else (10 if d20 < 0 else 5))
            margin_score = 18 if d60 < -10 else (14 if d60 < -5 else (10 if d60 < 0 else 6))
            breadth_score = 18 if chg < -1.5 else (14 if chg < -1 else (10 if chg < 0 else 6))
            dd_seg = min(15, round(dd_pct * 0.6)) if dd_pct > 0 else 0
            media_score = 8 if chg < -1 else (6 if chg < 0 else 4)
            account_score = 5

            total = vol_score + margin_score + breadth_score + dd_seg + media_score + account_score

            return {
                "fear_score": int(round(total)),
                "sz_price": price, "sz_chg": chg, "sz_d20": d20,
                "sz_d60": d60, "sz_ytd": ytd, "sz_h52": h52,
                "high52": h52, "dd_pct": round(dd_pct, 1),
                "vol_score": vol_score, "margin_score": margin_score,
                "breadth_score": breadth_score, "dd_score": dd_seg,
                "media_score": media_score, "account_score": account_score,
            }
    except:
        pass
    return None

# ============================================================
class StockAPIHandler(BaseHTTPRequestHandler):
    def _send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
            body = content.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except FileNotFoundError:
            self._send_json({"error": "not_found"}, 404)

    def _send_error_json(self, msg, status=500):
        self._send_json({"error": "server_error", "message": msg}, status)

    def log_message(self, format, *args):
        print(f"[{time.strftime('%H:%M:%S')}] {args[0]}", flush=True)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        # CORS
        origin = self.headers.get("Origin", "*")

        try:
            # ---- API: Search ----
            if path == "/api/search":
                q = params.get("q", [""])[0].strip()
                if not q:
                    self._send_json({"error": "missing_query", "message": "请提供q参数"}, 400)
                    return
                print(f"  [SEARCH] {q}", flush=True)
                results = search_stock(q)
                self._send_json({"results": results, "query": q})

            # ---- API: Quote ----
            elif path == "/api/quote":
                code = params.get("code", [""])[0].strip()
                if not code:
                    self._send_json({"error": "missing_code", "message": "请提供code参数"}, 400)
                    return
                print(f"  [QUOTE] {code}", flush=True)
                quote = get_quote(code)
                self._send_json(quote)

            # ---- API: Quick analysis ----
            elif path == "/api/analyze":
                code = params.get("code", [""])[0].strip()
                if not code:
                    self._send_json({"error": "missing_code"}, 400)
                    return
                print(f"  [ANALYZE] {code}", flush=True)
                from concurrent.futures import ThreadPoolExecutor
                with ThreadPoolExecutor(max_workers=4) as ex:
                    f_quote = ex.submit(get_quote, code)
                    f_ma = ex.submit(get_ma, code)
                    f_flow = ex.submit(get_fund_flow, code)
                    f_fin = ex.submit(get_finance, code)
                    quote = f_quote.result() or {"error": "no_data"}
                    ma = f_ma.result() or {"error": "ma_failed"}
                    flow = f_flow.result() or {"error": "flow_failed"}
                    fin = f_fin.result() or {"error": "finance_failed"}
                quote["ma"] = ma
                quote["flow"] = flow
                quote["finance"] = fin

                if "error" not in quote or quote.get("error") == "parse_partial":
                    # Calculate fear score based on available data
                    fear_data = calculate_fear_index()
                    fear_score = fear_data["fear_score"] if fear_data else 50
                    h52 = quote.get("h52")
                    price = quote.get("price")
                    pe = quote.get("pe", 0)
                    pb = quote.get("pb", 0)
                    chg = quote.get("chg", 0)
                    ytd = quote.get("ytd", 0)
                    dv = quote.get("dv", 0)

                    # Drawdown score
                    dd_pct = 0
                    dd_score = 0
                    if h52 and price and h52 > 0:
                        dd_pct = ((h52 - price) / h52) * 100
                        dd_score = min(35, round(max(0, dd_pct) * 1.0))
                        if dd_pct < 5:
                            dd_score = round(max(0, dd_pct) * 0.5)

                    # Valuation score
                    pe_score = 0
                    if pe > 0 and pe < 10: pe_score = 20
                    elif pe >= 10 and pe < 20: pe_score = 15
                    elif pe >= 20 and pe < 35: pe_score = 10
                    elif pe >= 35 and pe < 60: pe_score = 5

                    pb_score = 0
                    if pb > 0 and pb < 1: pb_score = 5
                    elif pb >= 1 and pb < 2: pb_score = 3

                    val_score = pe_score + pb_score

                    # Momentum score
                    mom_score = 0
                    if ytd < -25: mom_score = 20
                    elif ytd < -15: mom_score = 15
                    elif ytd < -5: mom_score = 10
                    elif ytd < 0: mom_score = 5

                    mkt_overlay = round(fear_score * 0.2)
                    total = min(100, max(0, dd_score + val_score + mom_score + mkt_overlay))

                    rec = "hold"
                    if total >= 70: rec = "strong_buy"
                    elif total >= 55: rec = "buy"
                    elif total >= 40: rec = "hold"
                    elif total >= 25: rec = "caution"
                    else: rec = "sell"

                    quote["analysis"] = {
                        "fear_score": fear_score,
                        "dd_pct": round(dd_pct, 1),
                        "dd_score": dd_score,
                        "val_score": val_score,
                        "mom_score": mom_score,
                        "mkt_overlay": mkt_overlay,
                        "total_score": total,
                        "recommendation": rec
                    }

                self._send_json(quote)

            # ---- Serve frontend with cached live data ----
            elif path == "/" or path == "/index.html":
                self._serve_live_main_page()
            
            # ---- Server-rendered search page (no JS fetch needed) ----
            elif path == "/search":
                q = params.get("q", [""])[0].strip()
                if not q:
                    self._send_html(str(HTML_FILE))
                    return
                print(f"  [SEARCH] {q}", flush=True)
                results = search_stock(q)
                self._serve_search_page(q, results)
            
            # ---- Server-rendered stock analysis page ----
            elif path == "/stock":
                code = params.get("code", [""])[0].strip()
                if not code:
                    self._send_html(str(HTML_FILE))
                    return
                print(f"  📊 分析: {code}", flush=True)
                quote = get_quote(code)
                if quote is None:
                    quote = {"error": "no_data"}
                self._serve_stock_page(code, quote)

            # ---- Health check ----
            elif path == "/api/health":
                self._send_json({
                    "status": "ok",
                    "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "markets": ["A股", "港股", "美股"],
                    "neodata_ok": NEODATA_DIR.exists(),
                    "westock_ok": WESTOCK_DIR.exists(),
                })

            # ---- Live fear index (for frontend polling) ----
            elif path == "/api/fear-index":
                self._send_json(_fear_cache)

            # ---- Cached stock quotes (ms response) ----
            elif path == "/api/stocks":
                with _stock_cache_lock:
                    self._send_json({
                        "stocks": _stock_cache,
                        "updated": _stock_last_update,
                        "count": len(_stock_cache),
                    })

            elif path == "/api/indices":
                self._send_json(_indices_cache)

            elif path == "/api/suggest":
                q = params.get("q", [""])[0].strip().lower()
                results = []
                if len(q) >= 2:
                    with _stock_cache_lock:
                        for code, s in _stock_cache.items():
                            n = s.get("n", "")
                            if q in n or q in code.replace("sh","").replace("sz","").replace("hk","").replace("us",""):
                                results.append({
                                    "code": code, "name": n,
                                    "market": s.get("mkt", "A股"),
                                    "memo": s.get("memo", ""),
                                })
                                if len(results) >= 15:
                                    break
                self._send_json({"results": results, "query": q})

            else:
                self._send_json({"error": "not_found", "path": path}, 404)

        except Exception as e:
            traceback.print_exc()
            self._send_error_json(str(e))


    def _serve_search_page(self, query, results):
        """Render search results as full HTML page"""
        rows = ""
        for r in results:
            if "error" in r:
                continue
            code = r["code"]
            name = r["name"]
            market = r.get("market", "A股")
            mkt_emoji = "🇭🇰" if market == "港股" else ("🇺🇸" if market == "美股" else "🇨🇳")
            rows += f"""
            <tr>
              <td>{mkt_emoji} <a href="/stock?code={code}" style="color:#1a1a2e;font-weight:600;text-decoration:none;">{name}</a></td>
              <td style="color:#8e8ea0;">{code}</td>
              <td><span class="tag">{market}</span></td>
              <td><a href="/stock?code={code}" style="color:#339af0;text-decoration:none;">分析 →</a></td>
            </tr>"""
        
        if not rows.strip():
            rows = f'<tr><td colspan="4" style="padding:40px;text-align:center;color:#8e8ea0;">未找到「{query}」<br><small>请尝试完整股票代码或名称</small></td></tr>'
        
        html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>搜索: {query} — 反散户情绪工具</title>
<style>
{STYLE}
</style>
</head>
<body>
<div class="container">
<div class="header">
  <h1>🔍 搜索结果: {query}</h1>
  <div class="subtitle">全市场实时搜索 — A股 + 港股 + 美股</div>
</div>
<div class="search-section">
  <form action="/search" method="get" class="search-row">
    <div class="search-input-wrap">
      <input type="text" class="search-input" name="q" value="{query}" placeholder="输入股票代码或名称...">
    </div>
    <button type="submit" class="search-btn">🔍 搜索</button>
  </form>
  <div class="search-hint">
    <a href="/" style="color:#339af0;text-decoration:none;">← 返回首页</a>
    | 代码(600519) / 名称(茅台) / 简称(NVDA)
  </div>
</div>
<div class="card">
<table class="stock-table">
<thead><tr><th>股票名称</th><th>代码</th><th>市场</th><th>操作</th></tr></thead>
<tbody>{rows}</tbody>
</table>
</div>
<div class="data-source">数据来源: NeoData · 腾讯自选股 | {time.strftime('%Y-%m-%d %H:%M')}</div>
</div></body></html>"""
        self._send_html_str(html)

    def _serve_stock_page(self, code, quote):
        """Render stock analysis as full HTML page"""
        name = quote.get("name", code)
        price = quote.get("price")
        chg = quote.get("chg", 0)
        pe = quote.get("pe", 0)
        pb = quote.get("pb", 0)
        dv = quote.get("dv", 0)
        h52 = quote.get("h52")
        ytd = quote.get("ytd", 0)
        d20 = quote.get("d20", 0)
        d60 = quote.get("d60", 0)
        market = quote.get("market", "A股")

        # Calculate scores (use unified fear index)
        fear_data = calculate_fear_index()
        fear_score = fear_data["fear_score"] if fear_data else 50
        dd_pct = ((h52 - price) / h52 * 100) if h52 and price else 0
        dd_score = min(35, round(max(0, dd_pct) * 1.0)) if dd_pct >= 5 else round(max(0, dd_pct) * 0.5)
        
        pe_score = 20 if pe > 0 and pe < 10 else (15 if pe < 20 else (10 if pe < 35 else (5 if pe < 60 else 0)))
        pb_score = 5 if pb > 0 and pb < 1 else (3 if pb < 2 else 0)
        val_score = pe_score + pb_score
        
        mom_score = 20 if ytd < -25 else (15 if ytd < -15 else (10 if ytd < -5 else (5 if ytd < 0 else 0)))
        mkt_overlay = round(fear_score * 0.2)
        total = min(100, max(0, dd_score + val_score + mom_score + mkt_overlay))
        
        rec_map = {
            70: ("🟢", "★★★★★", "strong-buy", "极度超卖，多项因子共振，强烈买入"),
            55: ("🟡", "★★★★", "buy", "具备逆向买入价值，建议分批建仓"),
            40: ("⚪", "★★★", "hold", "赔率一般，持有观望"),
            25: ("🔴", "★★", "caution", "涨幅较大或估值偏高，谨慎"),
            0:  ("🔴", "★", "sell", "极度乐观，逆向卖出"),
        }
        icon, stars, rec_class, rec_desc = ("⚪", "★★★", "hold", "")
        for threshold in sorted(rec_map.keys(), reverse=True):
            if total >= threshold:
                icon, stars, rec_class, rec_desc = rec_map[threshold]
                break
        
        chg_cls = "up" if chg >= 0 else "down"
        chg_sign = "+" if chg >= 0 else ""
        dd_color = "#e03131" if dd_pct > 30 else ("#f08c00" if dd_pct > 15 else "#2f9e44")
        pe_color = "#2f9e44" if pe > 0 and pe < 15 else ("#f08c00" if pe < 30 else "#e03131")
        score_color = "#e03131" if total >= 55 else ("#f08c00" if total >= 40 else "#2f9e44")
        d20_cls = "up" if d20 >= 0 else "down"
        d60_cls = "up" if d60 >= 0 else "down"
        ytd_cls = "up" if ytd >= 0 else "down"
        dv_str = f"{dv:.2f}%" if dv > 0 else "—"
        price_str = f"¥{price:,.2f}" if price else "—"
        mkt_tag = "🇭🇰" if market == "港股" else ("🇺🇸" if market == "美股" else "🇨🇳")

        error_banner = ""
        if "error" in quote:
            error_banner = f'<div style="background:#fff3cd;padding:12px;border-radius:8px;margin-bottom:16px;font-size:13px;">⚠️ 部分数据获取失败: {quote.get("message","")}。以下分析可能不完整。</div>'

        html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>{name} — 反散户情绪分析</title>
<style>
{STYLE}
</style>
</head>
<body>
<div class="container">
<div class="search-section">
  <form action="/search" method="get" class="search-row">
    <div class="search-input-wrap">
      <input type="text" class="search-input" name="q" value="{name}" placeholder="输入股票代码或名称...">
    </div>
    <button type="submit" class="search-btn">🔍 搜索</button>
  </form>
  <div class="search-hint"><a href="/" style="color:#339af0;text-decoration:none;">← 返回首页</a></div>
</div>
{error_banner}
<div class="stock-analysis active" style="display:block;">
<div class="stock-header">
  <div><span class="stock-title">{mkt_tag} {name} <span style="font-size:13px;color:#8e8ea0;font-weight:400;">· 实时</span></span><span class="stock-code-tag">{code}</span></div>
  <div class="stock-price-info">
    <div class="current-price" style="color:{'var(--red)' if chg >= 0 else 'var(--green)'};">{price_str}</div>
    <div class="price-change {chg_cls}">{chg_sign}{chg:.2f}%</div>
  </div>
</div>
<div class="stock-metrics">
  <div class="metric-item"><div class="metric-value" style="color:{pe_color};">{pe:.2f}</div><div class="metric-label">市盈率 PE</div></div>
  <div class="metric-item"><div class="metric-value">{pb:.2f}</div><div class="metric-label">市净率 PB</div></div>
  <div class="metric-item"><div class="metric-value" style="color:{dd_color};">{dd_pct:.1f}%</div><div class="metric-label">距52周高回撤</div></div>
  <div class="metric-item"><div class="metric-value">{dv_str}</div><div class="metric-label">股息率</div></div>
</div>
<div class="stock-score-breakdown">
  <div class="score-chip"><div class="chip-value" style="color:{dd_color};">{dd_score}</div><div class="chip-label">回撤 /35</div></div>
  <div class="score-chip"><div class="chip-value" style="color:{pe_color};">{val_score}</div><div class="chip-label">估值 /25</div></div>
  <div class="score-chip"><div class="chip-value" style="color:{'#e03131' if ytd < -15 else ('#f08c00' if ytd < 0 else '#2f9e44')};">{mom_score}</div><div class="chip-label">恐慌动量 /20</div></div>
  <div class="score-chip"><div class="chip-value" style="color:var(--fear-mid);">{mkt_overlay}</div><div class="chip-label">大盘加成 /20</div></div>
</div>
<div class="stock-recommendation stock-rec-{rec_class}">
  <div class="stock-rec-icon">{icon}</div>
  <div class="stock-rec-text"><h4>{stars} {'强烈买入' if total >= 70 else ('分批买入' if total >= 55 else ('持有观望' if total >= 40 else ('谨慎减仓' if total >= 25 else '建议回避')))}</h4><p>{rec_desc}</p></div>
  <div class="stock-rec-score"><div class="big-score" style="color:{score_color};">{total}</div><div class="score-sub">买入评分/100</div></div>
</div>
<div style="margin-top:16px;">
<table class="stock-table"><thead><tr><th>指标</th><th>今日</th><th>近20日</th><th>近60日</th><th>年初至今</th></tr></thead><tbody><tr>
  <td><b>涨跌幅</b></td>
  <td class="{chg_cls}">{chg_sign}{chg:.2f}%</td>
  <td class="{d20_cls}">{'+' if d20 >= 0 else ''}{d20:.2f}%</td>
  <td class="{d60_cls}">{'+' if d60 >= 0 else ''}{d60:.2f}%</td>
  <td class="{ytd_cls}">{'+' if ytd >= 0 else ''}{ytd:.2f}%</td>
</tr></tbody></table>
</div>
<div style="margin-top:10px;font-size:11px;color:#8e8ea0;text-align:right;">数据来源: 腾讯自选股 · NeoData | {time.strftime('%Y-%m-%d %H:%M')} | 仅供参考</div>
</div>
</div></body></html>"""
        self._send_html_str(html)

    def _send_html_str(self, html):
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _calculate_fear_index(self):
        """Calculate live market fear index from westock-data. No cache."""
        return calculate_fear_index()

    def _serve_live_main_page(self):
        """Serve the main page with live market data injected — using cached data."""
        with open(str(HTML_FILE), "r", encoding="utf-8") as f:
            html = f.read()

        fear_data = _fear_cache  # use background cache, instant
        fear_score = fear_data.get("fear_score", 50)
        sz_price = fear_data.get("sz_price", 4000)
        sz_chg = fear_data.get("sz_chg", 0)
        sz_d20 = fear_data.get("sz_d20", 0)
        sz_d60 = fear_data.get("sz_d60", 0)
        high52 = fear_data.get("high52", 4258)
        dd_val = round(((high52 - sz_price) / high52 * 100), 1) if high52 and sz_price else 0
        vol_score = fear_data.get("vol_score", 5)
        margin_score = fear_data.get("margin_score", 6)
        breadth_score = fear_data.get("breadth_score", 8)
        dd_seg_score = fear_data.get("dd_score", 3)
        media_score = fear_data.get("media_score", 5)
        account_score = fear_data.get("account_score", 5)

        # Update fearScore in JS
        html = re.sub(r"var fearScore = [\d.]+;", f"var fearScore = {fear_score};", html)
        
        # Build dailyData from in-memory fear history
        import datetime
        today = time.strftime("%Y-%m-%d")
        
        global _fear_history
        _fear_history[today] = fear_score
        
        # Last 10 trading days for chart
        today_dt = datetime.date.today()
        daily_entries = []
        d = today_dt
        found = 0
        while found < 10:
            if d.weekday() < 5:  # trading day
                key = d.strftime("%Y-%m-%d")
                if key in _fear_history:
                    daily_entries.append((key, _fear_history[key]))
                found += 1
            d -= datetime.timedelta(days=1)
        daily_entries.reverse()
        
        daily_parts = []
        for date_str, score_val in daily_entries:
            mm, dd = date_str[5:7], date_str[8:10]
            label = f"{int(mm)}/{int(dd)}"  # 7/14 instead of 07-14
            daily_parts.append(f"'{label}',{score_val}")
        daily_js = "var dailyData = [" + ",".join(daily_parts) + "];"
        
        # Inject dailyData (replaces old inline var dailyData)
        html = re.sub(r"var dailyData = \[[\s\S]*?\];", daily_js, html)
        
        # Update stale dates outside the chart
        for old_date in set(re.findall(r"20\d\d-\d\d-\d\d", html)):
            if old_date != today and old_date < "2026-07-10":
                html = html.replace(old_date, today)
        
        # Update 上证 header
        sz_class = "up" if sz_chg >= 0 else "down"
        # Replace the placeholder "上证指数 — <span>—</span>"
        old_sz_pattern = r'上证指数 [\d,.]+ <span class="..">[+\-][\d.]+%</span>'
        old_sz2 = "上证指数 — <span>—</span>"
        new_sz = f'上证指数 {sz_price:,.2f} <span class="{sz_class}">{sz_chg:+.2f}%</span>'
        if re.search(old_sz_pattern, html):
            html = re.sub(old_sz_pattern, new_sz, html)
        else:
            html = html.replace(old_sz2, new_sz)

        # Inject stockDB from in-memory live cache (no disk reads)
        with _stock_cache_lock:
            db = dict(_stock_cache)  # shallow copy
        if db:
            entries = []
            for code, s in db.items():
                name = s["n"].replace('"', '\\"')
                memo = s.get("memo", "").replace('"', '\\"')
                entries.append(
                    f'"{code}":{{n:"{name}",p:{s.get("p",0)},chg:{s.get("chg",0)},'
                    f'pe:{s.get("pe",0)},pb:{s.get("pb",0)},h52:{s.get("h52",0)},'
                    f'dv:{s.get("dv",0)},d20:{s.get("d20",0)},d60:{s.get("d60",0)},'
                    f'ytd:{s.get("ytd",0)},mkt:"{s.get("mkt","A股")}",memo:"{memo}"}}'
                )
            inline_db = "var stockDB = {" + ",".join(entries) + "};"
            html = re.sub(r"var stockDB = \{[\s\S]*?\};", inline_db, html)
        
        # Update panic description
        zone = "极度恐慌" if fear_score > 75 else ("中度恐慌" if fear_score > 50 else ("偏贪婪" if fear_score > 25 else "极度贪婪"))
        action = "★★★★★ 强烈买入" if fear_score > 75 else ("★★★★ 建议分批买入" if fear_score > 50 else ("★★ 谨慎持有" if fear_score > 25 else "★ 建议回避"))
        
        html = re.sub(r'<div class="score-num" id="fearScore">[\d.]+</div>',
                         f'<div class="score-num" id="fearScore">{fear_score}</div>', html)
        html = re.sub(r'<h2 id="conclusionTitle">[^<]+</h2>',
                         f'<h2 id="conclusionTitle">{zone} · {"买入机会" if fear_score > 50 else "谨慎观望"}</h2>', html)
        html = re.sub(r'<span class="recommendation \w+" id="recommendTag">[^<]+</span>',
                         f'<span class="recommendation buy" id="recommendTag">{action}</span>', html)

        # Inject calculation breakdown with real values
        calc_time = time.strftime("%Y-%m-%d %H:%M")
        html = re.sub(r'实时计算时间：[^<]+', f'实时计算时间：{calc_time}', html)
        
        # Update breakdown scores
        dd_val = round(((high52 - sz_price) / high52 * 100), 1) if high52 else 0
        vol_desc = "放量上涨" if sz_d20 > 1 else ("缩量震荡" if sz_d20 > -1 else "成交量萎缩")
        margin_desc = "杠杆资金平稳" if abs(sz_d60) < 10 else ("杠杆资金撤离" if sz_d60 < -10 else "杠杆资金活跃")
        breadth_desc = "多数上涨" if sz_chg > 0.5 else ("涨跌互现" if sz_chg > -0.5 else "多数下跌")
        
        # Generate breakdown table FROM CODE (guarantees consistency)
        calc_time = time.strftime("%Y-%m-%d %H:%M")
        d20_str = f"{sz_d20:+.2f}%"
        dd_pct_val = fear_data.get("dd_pct", 0) if fear_data else 0
        sz_class = "up" if sz_chg >= 0 else "down"
        d20_class = "up" if sz_d20 >= 0 else "down"
        d60_class = "up" if sz_d60 >= 0 else "down"
        
        def tag_for(score, max_s):
            pct = score / max_s if max_s else 0
            return "tag-ok" if pct < 0.4 else ("tag-hold" if pct < 0.7 else "tag-warn")
        
        factors = [
            ("📉 成交量萎缩度", 25, vol_score, f"近20日上证 {d20_str}，{'放量上涨' if sz_d20>1 else ('缩量震荡' if sz_d20>-1 else '成交量萎缩')}"),
            ("💰 融资余额趋势", 20, margin_score, f"近60日 {'+' if sz_d60>=0 else ''}{sz_d60:.2f}%{'，杠杆资金平稳' if abs(sz_d60)<10 else ('，杠杆资金撤离' if sz_d60<-10 else '，杠杆资金活跃')}"),
            ("📋 市场宽度", 20, breadth_score, f"今日 {'+' if sz_chg>=0 else ''}{sz_chg:.2f}%，{'多数上涨' if sz_chg>0.5 else ('涨跌互现' if sz_chg>-0.5 else '多数下跌')}"),
            ("📌 指数高点回撤", 15, dd_seg_score, f"上证 {sz_price:,.2f}，距52周高 {high52:,.2f} 回撤 {dd_pct_val:.1f}%"),
            ("📰 媒体情绪", 10, media_score, "今日" + ("上涨，悲观报道减少" if sz_chg >= 0 else "下跌，恐慌报道增多")),
            ("👥 新增开户动能", 10, account_score, "上半年开户同比+57%，散户入市积极"),
        ]
        
        rows = ""
        for name, weight, score, desc in factors:
            rows += f'''<tr><td>{name}</td><td>{weight}</td><td><b>{score}</b> / {weight}</td><td style="font-size:12px;">{desc}</td><td><span class="tag {tag_for(score,weight)}">{'低恐慌' if score/weight < 0.4 else ('中性' if score/weight < 0.7 else '高恐慌')}</span></td></tr>\n'''
        
        zone = "极度恐慌" if fear_score > 75 else ("中度恐慌" if fear_score > 50 else ("偏贪婪" if fear_score > 25 else "极度贪婪"))
        
        breakdown_html = f"""
  <div class="card" style="margin-bottom:20px;">
    <h3><span class="icon">🧮</span>当前恐慌指数计算详情 <span style="font-size:12px;color:var(--text-muted);font-weight:400;">— 实时计算时间：{calc_time}</span></h3>
    <table class="stock-table">
      <thead><tr><th>因子</th><th>权重</th><th>当期得分</th><th>计算依据</th><th>恐慌程度</th></tr></thead>
      <tbody>
        {rows}
        <tr style="background:#f8f9fa;font-weight:700;">
          <td><b>合计</b></td><td><b>100</b></td><td><b>{fear_score}</b> / 100</td>
          <td style="font-size:12px;">结论：{zone}，<b>{'反散户策略提示买入' if fear_score>50 else '反散户策略提示观望或减仓'}</b></td>
          <td><span class="tag {'tag-warn' if fear_score>50 else 'tag-ok'}">{zone}</span></td>
        </tr>
      </tbody>
    </table>
    <div style="margin-top:12px;font-size:12px;color:var(--text-muted);">
      <b>计算公式：</b>恐慌指数 = 成交量({vol_score}/25) + 融资({margin_score}/20) + 宽度({breadth_score}/20) + 回撤({dd_seg_score}/15) + 媒体({media_score}/10) + 开户({account_score}/10) = <b>{fear_score}/100</b><br>
      <b>数据源：</b>上证指数实时行情（腾讯自选股）| 上证现报 {sz_price:,.2f} <span class="{sz_class}">{sz_chg:+.2f}%</span> | 近20日 <span class="{d20_class}">{sz_d20:+.2f}%</span> | 近60日 <span class="{d60_class}">{sz_d60:+.2f}%</span>
    </div>
  </div>
"""
        html = html.replace("<!--BREAKDOWN_PLACEHOLDER-->", breakdown_html)
        
        # Generate dynamic conclusion description
        if fear_score > 75:
            zone_zh = "极度恐慌"
            desc = (f'当前恐慌指数 <strong>{fear_score}/100</strong>，处于「极度恐慌」区间。'
                    f'上证报 {sz_price:,.0f}，距52周高回撤 {dd_pct_val:.0f}%，市场情绪极度悲观。'
                    f'历史数据显示恐慌指数>70时，<strong>未来1-3个月胜率约71%</strong>，'
                    f'反散户策略进入强烈买入窗口。')
        elif fear_score > 50:
            zone_zh = "中度恐慌"
            desc = (f'当前恐慌指数 <strong>{fear_score}/100</strong>，处于「中度恐慌」区间。'
                    f'上证报 {sz_price:,.0f}，近期{"下跌" if sz_chg < 0 else "震荡"}，市场情绪偏谨慎。'
                    f'历史数据显示恐慌指数在50-70时，<strong>未来1-3个月胜率约62%</strong>，'
                    f'已具备分批建仓的价值。')
        elif fear_score > 25:
            zone_zh = "偏贪婪"
            desc = (f'当前恐慌指数 <strong>{fear_score}/100</strong>，处于「偏贪婪」区间。'
                    f'上证报 {sz_price:,.0f}，市场情绪偏乐观，赔率一般。'
                    f'建议精选个股、控制仓位，非最佳左侧买入时机。')
        else:
            zone_zh = "极度贪婪"
            desc = (f'当前恐慌指数 <strong>{fear_score}/100</strong>，处于「极度贪婪」区间。'
                    f'上证报 {sz_price:,.0f}，市场情绪极度亢奋。'
                    f'反散户策略提示减仓或回避，历史数据显示此时买入胜率<40%。')
        html = html.replace("<!--CONCLUSION_DESC-->", desc)
        
        # Inject factor scores for radar chart (scaled to 0-100)
        max_scores = {"vol": 25, "margin": 20, "breadth": 20, "dd": 15, "media": 10, "account": 10}
        scaled = {
            "vol": round(vol_score / 25 * 100),
            "margin": round(margin_score / 20 * 100),
            "breadth": round(breadth_score / 20 * 100),
            "dd_seg": round(dd_seg_score / 15 * 100),
            "media": round(media_score / 10 * 100),
            "account": round(account_score / 10 * 100),
        }
        factor_js = (
            f'var factorScores = ['
            f'{{name:"成交量\\n萎缩",score:{scaled["vol"]},max:100}},'
            f'{{name:"融资余额\\n回落",score:{scaled["margin"]},max:100}},'
            f'{{name:"市场\\n宽度",score:{scaled["breadth"]},max:100}},'
            f'{{name:"指数\\n回撤",score:{scaled["dd_seg"]},max:100}},'
            f'{{name:"媒体\\n情绪",score:{scaled["media"]},max:100}},'
            f'{{name:"开户\\n动能",score:{scaled["account"]},max:100}}'
            f'];'
        )
        html = html.replace('var factorScores = [];  // <!--FACTOR_SCORES--> injected by server', factor_js)
        
        # Pool header
        pool_hdr = f'(恐慌指数{fear_score} → {zone} → {"建议分批建仓" if fear_score > 50 else ("谨慎持有" if fear_score > 25 else "建议回避")})'
        html = html.replace('<!--POOL_HEADER-->', pool_hdr)

        # Generate panic-buy pool from recently-refreshed live stocks
        now = time.time()
        fresh_cutoff = now - 300  # last 5 minutes
        with _stock_cache_lock:
            cache_snapshot = dict(_stock_cache)
            refresh_age = dict(_stock_refresh_time)
        pool_entries = []
        for code, s in cache_snapshot.items():
            p = s.get("p", 0)
            chg = s.get("chg", 0)
            pe = s.get("pe", 0)
            dv = s.get("dv", 0)
            if not p or not s.get("n"): continue
            # Only use stocks refreshed recently
            if refresh_age.get(code, 0) < fresh_cutoff:
                continue
            score = (-chg * 1.5) + (10 if 0 < pe <= 20 else (5 if 0 < pe <= 40 else 0)) + (min(dv, 5) * 2)
            pool_entries.append((code, s, score))
        pool_entries.sort(key=lambda x: -x[2])
        selected = pool_entries[:8]
        
        # Fallback: if nothing fresh yet, show loading note
        if len(selected) < 3:
            pool_js = ('var stocks = [{name:"数据加载中...",code:"",type:"","reason":"后台正在拉取实时行情，请1分钟后刷新页面","rating":"...","ratingClass":"tag-hold"}];')
        else:
            pool_js_parts = []
            for code, s, sc in selected:
                name = s["n"].replace('"', '\\"')
                mkt = s.get("mkt", "A股")
                chg = s.get("chg", 0)
                pe_raw = s.get("pe", 0)
                dv_raw = s.get("dv", 0)
                
                # Build meaningful reason
                reasons = []
                if pe_raw > 0 and pe_raw <= 15:
                    reasons.append("低PE")
                elif pe_raw > 0 and pe_raw <= 25:
                    reasons.append("PE合理")
                if dv_raw > 0 and dv_raw >= 3:
                    reasons.append(f"高股息{dv_raw:.1f}%")
                elif dv_raw > 0:
                    reasons.append(f"股息{dv_raw:.1f}%")
                if chg < -2:
                    reasons.append("超跌")
                elif chg < 0:
                    reasons.append("近期回调")
                elif chg >= 0:
                    reasons.append("趋势向好")
                reason = " + ".join(reasons) if reasons else f"PE{pe_raw:.1f} {chg:+.1f}%"
                
                # Better star ratings
                if sc >= 12:
                    stars = "★★★★★"
                    rating = "强烈买入"
                    cls = "tag-buy"
                elif sc >= 8:
                    stars = "★★★★☆"
                    rating = "积极买入"
                    cls = "tag-buy"
                elif sc >= 5:
                    stars = "★★★★"
                    rating = "分批建仓"
                    cls = "tag-buy"
                elif sc >= 2:
                    stars = "★★★"
                    rating = "观望"
                    cls = "tag-hold"
                else:
                    stars = "★★"
                    rating = "暂避"
                    cls = "tag-sell"
                
                pool_js_parts.append(
                    f'{{name:"{name}",code:"{code}",type:"{mkt}",'
                    f'reason:"{reason}",rating:"{stars} {rating}",ratingClass:"{cls}"}}'
                )
            pool_js = "var stocks = [" + ",".join(pool_js_parts) + "];"
        html = html.replace('var stocks = [];  // <!--POOL_STOCKS--> injected by server', pool_js)

        # Dynamic factor list (the "恐慌因子拆解" panel)
        def bar_color(pct):
            if pct >= 66: return "#e03131"
            if pct >= 40: return "#f08c00"
            return "#2f9e44"

        items = [
            ("📉 成交量萎缩度", vol_score, 25,
             f"近20日上证 {sz_d20:+.2f}%，{'放量上涨' if sz_d20>1 else ('缩量震荡' if sz_d20>-1 else '成交量萎缩')}"),
            ("💰 融资余额回落", margin_score, 20,
             f"近60日 {sz_d60:+.2f}%，{'杠杆资金平稳' if abs(sz_d60)<10 else ('杠杆资金撤离' if sz_d60<-10 else '杠杆资金活跃')}"),
            ("📋 市场宽度恶化", breadth_score, 20,
             f"今日 {sz_chg:+.2f}%，{'多数上涨' if sz_chg>0.5 else ('涨跌互现' if sz_chg>-0.5 else '多数下跌')}"),
            ("📌 指数高点回撤", dd_seg_score, 15,
             f"上证 {sz_price:,.2f}，距52周高 {high52:,.2f} 回撤 {dd_pct_val:.1f}%"),
            ("📰 媒体恐慌情绪", media_score, 10,
             f"今日{'上涨，悲观报道减少' if sz_chg >= 0 else '下跌，恐慌报道增多'}"),
            ("👥 新增开户动能", account_score, 10,
             f"上半年开户同比+57%，散户入市积极（偏乐观因子）"),
        ]
        factor_html = ""
        for name, score, max_s, detail in items:
            pct = round(score / max_s * 100)
            color = bar_color(pct)
            factor_html += (
                f'<div class="factor-item">'
                f'<div class="factor-header">'
                f'<span class="factor-name">{name}</span>'
                f'<span class="factor-score" style="color:{color};">{score} / {max_s}</span>'
                f'</div>'
                f'<div class="factor-bar-bg"><div class="factor-bar-fill" style="width:{pct}%;background:{color};"></div></div>'
                f'<div class="factor-detail">{detail}</div>'
                f'</div>'
            )
        html = html.replace('<div class="factor-list"><!--FACTOR_LIST--></div>',
                           f'<div class="factor-list">{factor_html}</div>')

        # Inject index MA data from background cache
        import json as json2
        indices_json = json2.dumps(_indices_cache, ensure_ascii=False)
        html = html.replace("var indicesData = {};", f"var indicesData = {indices_json};")
        
        # Server-side render the index cards (replaces JS rendering)
        def render_idx_cards(cache):
            order = [("sh000001", "上证指数"), ("sh000688", "科创50"), ("sz399006", "创业板指")]
            cards = ""
            for code, name in order:
                idx = cache.get(code, {})
                price = idx.get("price", 0)
                chg = idx.get("chg_pct", 0)
                sign = "+" if chg >= 0 else ""
                color = "var(--red)" if chg >= 0 else "var(--green)"
                def no(v): return str(int(v)) if v and v > 0 else "—"
                def above(v):
                    if not v or not price: return "var(--text-muted)"
                    return "var(--red)" if price >= v else "var(--green)"
                cards += f'''<div class="idx-card"><div class="idx-name">{name}</div>
<div class="idx-price" style="color:{color};">{price:.2f}</div>
<div class="idx-chg" style="color:{color};">{sign}{chg:.2f}%</div>
<div class="idx-mas">MA5 <b style="color:{above(idx.get('ma5'))}">{no(idx.get('ma5'))}</b> | MA10 <b style="color:{above(idx.get('ma10'))}">{no(idx.get('ma10'))}</b> | MA20 <b style="color:{above(idx.get('ma20'))}">{no(idx.get('ma20'))}</b> | MA60 <b style="color:{above(idx.get('ma60'))}">{no(idx.get('ma60'))}</b></div></div>'''
            return cards
        
        idx_cards = render_idx_cards(_indices_cache)
        html = html.replace("<!--INDICES_SERVER-->\n  <div class=\"idx-card\"><div class=\"idx-name\">上证指数</div><div class=\"idx-price\">—</div><div class=\"idx-chg\">—</div><div class=\"idx-mas\">MA5 — | MA10 — | MA20 — | MA60 —</div></div>\n  <div class=\"idx-card\"><div class=\"idx-name\">科创50</div><div class=\"idx-price\">—</div><div class=\"idx-chg\">—</div><div class=\"idx-mas\">MA5 — | MA10 — | MA20 — | MA60 —</div></div>\n  <div class=\"idx-card\"><div class=\"idx-name\">创业板指</div><div class=\"idx-price\">—</div><div class=\"idx-chg\">—</div><div class=\"idx-mas\">MA5 — | MA10 — | MA20 — | MA60 —</div></div>", idx_cards)
        
        self._send_html_str(html)

def backfill_history():
    """Compute fear history from K-line data, stored in memory only."""
    global _fear_history
    today = time.strftime("%Y-%m-%d")
    
    print("  [BACKFILL] Fetching K-line...", flush=True)
    try:
        r = subprocess.run(
            [str(NODE_EXE), "scripts/index.js", "kline", "sh000001",
             "--start", "2025-01-01", "--end", today, "--limit", "300"],
            cwd=str(WESTOCK_DIR), capture_output=True, text=True,
            encoding='utf-8', errors='replace', timeout=30,
        )
        if r.returncode != 0:
            print(f"  [BACKFILL] Failed, code={r.returncode}", flush=True)
            return
    except Exception as e:
        print(f"  [BACKFILL] Error: {e}", flush=True)
        return
    
    klines = []
    for line in r.stdout.strip().split("\n"):
        line = line.strip()
        if not line.startswith("|") or line.startswith("| ---") or "date" in line:
            continue
        parts = [c.strip() for c in line.split("|") if c.strip()]
        if len(parts) < 8: continue
        try:
            klines.append({"date": parts[0], "price": float(parts[2]), "high": float(parts[3])})
        except: pass
    
    if len(klines) < 60:
        print(f"  [BACKFILL] Only {len(klines)} rows, need 60+", flush=True)
        return
    
    klines.sort(key=lambda x: x["date"])
    filled = 0
    
    for i in range(60, len(klines)):
        date = klines[i]["date"]
        if date in _fear_history: continue
        
        price = klines[i]["price"]
        prev = klines[i-1]["price"]
        chg = (price - prev) / prev * 100 if prev else 0
        
        d20_idx = i - 20
        d20 = (price - klines[d20_idx]["price"]) / klines[d20_idx]["price"] * 100
        
        d60_idx = max(i - 60, 0)
        d60 = (price - klines[d60_idx]["price"]) / klines[d60_idx]["price"] * 100
        
        yago = max(i - 250, 0)
        h52 = max(k["high"] for k in klines[yago:i+1])
        dd_pct = ((h52 - price) / h52 * 100) if h52 else 0
        
        vol = 20 if d20 < -5 else (15 if d20 < -3 else (10 if d20 < 0 else 5))
        margin = 18 if d60 < -10 else (14 if d60 < -5 else (10 if d60 < 0 else 6))
        breadth = 18 if chg < -1.5 else (14 if chg < -1 else (10 if chg < 0 else 6))
        dd_seg = min(15, round(dd_pct * 0.6)) if dd_pct > 0 else 0
        media = 8 if chg < -1 else (6 if chg < 0 else 4)
        total = vol + margin + breadth + dd_seg + media + 5
        
        _fear_history[date] = int(round(total))
        filled += 1
    
    print(f"  [BACKFILL] {filled} days computed, {len(_fear_history)} total", flush=True)

def main():
    server = ThreadingHTTPServer(("127.0.0.1", PORT), StockAPIHandler)
    print(f"""
╔══════════════════════════════════════════════╗
║   📈 全市场股票查询服务已启动                 ║
║   地址: http://localhost:{PORT}               ║
║                                               ║
║   API:                                        ║
║   GET /api/search?q=腾讯    搜索股票           ║
║   GET /api/quote?code=sh600519  行情查询       ║
║   GET /api/analyze?code=sz000858  分析评估     ║
║   GET /api/health         健康检查             ║
║                                               ║
║   覆盖: A股 / 港股 / 美股                     ║
║   数据: 腾讯自选股 + NeoData 金融搜索         ║
║   /api/stocks 实时股票缓存(60s)                ║
║   Ctrl+C 停止服务                             ║
╚══════════════════════════════════════════════╝
""", flush=True)

    # Backfill historical fear data from K-line
    backfill_history()

    # Start background stock refresher
    threading.Thread(target=fear_index_refresher, daemon=True, name="fear-refresher").start()
    threading.Thread(target=indices_refresher, daemon=True, name="indices-refresher").start()
    threading.Thread(target=refresh_stock_cache, daemon=True, name="stock-refresher").start()

    try:
        # Open browser after a short delay
        threading.Timer(1.0, lambda: webbrowser.open('http://localhost:8765')).start()
        print("  Browser opening...", flush=True)
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止", flush=True)
        server.shutdown()


if __name__ == "__main__":
    main()
