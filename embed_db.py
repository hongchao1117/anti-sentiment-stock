"""Embed stocks_db.json directly into the HTML for zero-network operation."""
import json, re

HTML = r"C:\Users\Administrator\Desktop\project\anti-sentiment-stock\anti-retail-sentiment.html"
DB = r"C:\Users\Administrator\Desktop\project\anti-sentiment-stock\stocks_db.json"

with open(HTML, "r", encoding="utf-8") as f:
    html = f.read()

with open(DB, "r", encoding="utf-8") as f:
    db = json.load(f)

# Build inline stockDB entries
entries = []
for code, s in db.items():
    n = s["n"].replace('"', '\\"')
    memo = s.get("memo", "").replace('"', '\\"')
    mkt = s.get("mkt", "A股")
    entries.append(
        f'"{code}":{{n:"{n}",p:{s.get("p",0)},chg:{s.get("chg",0)},'
        f'pe:{s.get("pe",0)},pb:{s.get("pb",0)},h52:{s.get("h52",0)},'
        f'dv:{s.get("dv",0)},d20:{s.get("d20",0)},d60:{s.get("d60",0)},'
        f'ytd:{s.get("ytd",0)},mkt:"{mkt}",memo:"{memo}"}}'
    )

inline_db = "var stockDB = {" + ",".join(entries) + "};"
print(f"Generated: {len(entries)} stocks, {len(inline_db)} chars")

# Replace the stockDB declaration
html = re.sub(
    r"var stockDB = \{[\s\S]*?\n\};",
    inline_db,
    html,
    count=1,
)

# Remove the external loader since data is now inline
html = re.sub(
    r"// Load external stock database if available[\s\S]*?loadExternalDB\(\);",
    f"// Stock database: {len(db)} stocks embedded inline (no network needed)",
    html,
    count=1,
)

# Also fix: when API is offline and local search returns nothing, check again
# The matchLocal function should still work since stockDB now has all stocks

with open(HTML, "w", encoding="utf-8") as f:
    f.write(html)

print("Done - HTML is now fully self-contained")
