#!/usr/bin/env python3
"""
全市场股票查询后端服务
支撑 A股/港股/美股 任意股票搜索与行情查询
端口: 8765
"""

import json
import os
import re
import subprocess
import sys
import time
import traceback
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

# ============================================================
# Config
# ============================================================
PORT = 8765
PROJECT_DIR = Path(__file__).resolve().parent
HTML_FILE = PROJECT_DIR / "anti-retail-sentiment.html"

NEODATA_DIR = Path(r"C:\Program Files\WorkBuddy\resources\app.asar.unpacked\resources\builtin-skills\neodata-financial-search")
WESTOCK_DIR = Path(r"C:\Program Files\WorkBuddy\resources\app.asar.unpacked\resources\builtin-skills\westock-data")

PYTHON_EXE = Path(r"C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12\python.exe")
NODE_EXE = Path(r"C:\Users\Administrator\.workbuddy\binaries\node\versions\22.22.2\node.exe")

# ============================================================
# Stock Search via neodata
# ============================================================
def search_stock(query: str) -> list:
    """搜索股票，返回匹配列表"""
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
        if result.returncode != 0:
            stderr = result.stderr.strip()
            if "TOKEN_EXPIRED" in stderr or "TOKEN_MISSING" in stderr:
                return [{"error": "token_expired", "message": "数据凭证已过期，请刷新"}]
            return [{"error": "search_failed", "message": stderr[:200]}]

        data = json.loads(result.stdout)
        stocks = []

        # Parse API recall results
        api_data = data.get("data", {}).get("apiData", {})
        entities = api_data.get("entity", [])
        api_recall = api_data.get("apiRecall", [])

        # Extract from entities (neodata sometimes returns code/name swapped)
        seen = set()
        for ent in entities:
            code = ent.get("code", "")
            name = ent.get("name", "")
            if code and name and (code not in seen):
                # Detect swapped code/name: name looks like a stock code
                if re.match(r'^\d{4,6}\.(SZ|SH|HK|US|NYSE|NASDAQ)$', name) and re.search(r'[\u4e00-\u9fff]', code):
                    code, name = name, code  # swap
                # Normalize: strip .SZ/.SH suffix for A-shares
                m = re.match(r'^(\d{6})\.(SZ|SH)$', code)
                if m:
                    digits = m.group(1)
                    suffix = m.group(2).lower()
                    code = f"{suffix}{digits}"
                
                market = "A股"
                if "HK" in code.upper() or code.endswith(".HK"):
                    market = "港股"
                elif any(code.endswith(s) for s in [".US", ".NYSE", ".NASDAQ"]) or code.isalpha():
                    market = "美股"
                stocks.append({
                    "code": code,
                    "name": name,
                    "market": market,
                    "price": None,
                    "chg": None
                })
                seen.add(code)

        # Also try to extract from apiRecall content
        for recall in api_recall:
            content = recall.get("content", "")
            # Try to find stock codes and names in content
            code_matches = re.findall(r'(?:sh|sz|SH|SZ)(\d{6})', content)
            for cm in code_matches[:10]:
                prefix = "sh" if "SH" in cm or content.find(f"sh{cm}") >= 0 else "sz"
                full_code = f"{prefix}{cm}"
                if full_code not in seen:
                    # Try to find name nearby
                    name_match = re.search(rf'{full_code}[^\n]*?([\u4e00-\u9fff]{{2,6}})', content)
                    stocks.append({
                        "code": full_code,
                        "name": name_match.group(1) if name_match else full_code,
                        "market": "A股",
                        "price": None,
                        "chg": None
                    })
                    seen.add(full_code)

        # If no results, try HK and US patterns
        if not stocks:
            hk_match = re.findall(r'(HK\d{4,5})|(\d{4,5}\.HK)', query.upper())
            if hk_match:
                for m in hk_match:
                    code = m[0] or m[1]
                    if code:
                        stocks.append({"code": code, "name": code, "market": "港股", "price": None, "chg": None})

            us_match = re.findall(r'([A-Z]{1,5})', query.upper())
            if us_match and not stocks:
                for m in us_match[:5]:
                    if len(m) >= 2 and m not in ('A', 'SH', 'SZ', 'HK', 'US'):
                        stocks.append({"code": m, "name": m, "market": "美股", "price": None, "chg": None})

        return stocks[:15] if stocks else [{"error": "no_results", "message": f"未找到「{query}」相关股票"}]

    except subprocess.TimeoutExpired:
        return [{"error": "timeout", "message": "查询超时，请重试"}]
    except json.JSONDecodeError as e:
        return [{"error": "parse_error", "message": f"数据解析失败: {str(e)[:100]}"}]
    except Exception as e:
        return [{"error": "exception", "message": str(e)[:200]}]


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
                print(f"  🔍 搜索: {q}", flush=True)
                results = search_stock(q)
                self._send_json({"results": results, "query": q})

            # ---- API: Quote ----
            elif path == "/api/quote":
                code = params.get("code", [""])[0].strip()
                if not code:
                    self._send_json({"error": "missing_code", "message": "请提供code参数"}, 400)
                    return
                print(f"  📊 行情: {code}", flush=True)
                quote = get_quote(code)
                self._send_json(quote)

            # ---- API: Quick analysis ----
            elif path == "/api/analyze":
                code = params.get("code", [""])[0].strip()
                if not code:
                    self._send_json({"error": "missing_code"}, 400)
                    return
                print(f"  📈 分析: {code}", flush=True)
                quote = get_quote(code)
                if quote is None:
                    quote = {"error": "no_data", "message": "未获取到行情数据"}

                if "error" not in quote or quote.get("error") == "parse_partial":
                    # Calculate fear score based on available data
                    fear_score = 67  # current market fear index
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

            # ---- Serve frontend ----
            elif path == "/" or path == "/index.html":
                self._send_html(str(HTML_FILE))

            # ---- Health check ----
            elif path == "/api/health":
                self._send_json({
                    "status": "ok",
                    "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "markets": ["A股", "港股", "美股"],
                    "neodata_ok": NEODATA_DIR.exists(),
                    "westock_ok": WESTOCK_DIR.exists(),
                })

            else:
                self._send_json({"error": "not_found", "path": path}, 404)

        except Exception as e:
            traceback.print_exc()
            self._send_error_json(str(e))


def main():
    server = HTTPServer(("127.0.0.1", PORT), StockAPIHandler)
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
║   Ctrl+C 停止服务                             ║
╚══════════════════════════════════════════════╝
""", flush=True)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止", flush=True)
        server.shutdown()


if __name__ == "__main__":
    main()
