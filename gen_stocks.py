#!/usr/bin/env python3
"""手动构建全市场核心股数据库 + 批量拉A股行情"""
import json, subprocess, time
from pathlib import Path

WESTOCK = Path(r"C:\Program Files\WorkBuddy\resources\app.asar.unpacked\resources\builtin-skills\westock-data")
NODE = Path(r"C:\Users\Administrator\.workbuddy\binaries\node\versions\22.22.2\node.exe")
OUT = Path(__file__).resolve().parent / "stocks_db.json"

# 从已有的17只开始
base = {
    "sh600036": {"n":"招商银行","mkt":"A股","memo":"银行龙头"},
    "sh600519": {"n":"贵州茅台","mkt":"A股","memo":"白酒龙头"},
    "sz300750": {"n":"宁德时代","mkt":"A股","memo":"电池龙头"},
    "sh688981": {"n":"中芯国际","mkt":"A股","memo":"芯片制造"},
    "sz002371": {"n":"北方华创","mkt":"A股","memo":"半导体设备"},
    "sh601688": {"n":"华泰证券","mkt":"A股","memo":"券商"},
    "sz300760": {"n":"迈瑞医疗","mkt":"A股","memo":"医疗器械"},
    "sh601318": {"n":"中国平安","mkt":"A股","memo":"保险"},
    "sz000858": {"n":"五粮液","mkt":"A股","memo":"白酒"},
    "sh600030": {"n":"中信证券","mkt":"A股","memo":"券商龙头"},
    "sz000333": {"n":"美的集团","mkt":"A股","memo":"家电龙头"},
    "sh601857": {"n":"中国石油","mkt":"A股","memo":"能源"},
    "sz002594": {"n":"比亚迪","mkt":"A股","memo":"新能源车"},
    "sh600276": {"n":"恒瑞医药","mkt":"A股","memo":"创新药"},
    "sz300059": {"n":"东方财富","mkt":"A股","memo":"互联网券商"},
    "sh688111": {"n":"金山办公","mkt":"A股","memo":"办公软件"},
    "sz000001": {"n":"平安银行","mkt":"A股","memo":"银行"},
}

# 补充更多A股核心标的
more_a = {
    "sh600900": "长江电力", "sh601398": "工商银行", "sh601939": "建设银行",
    "sh601288": "农业银行", "sh600036": "招商银行", "sh601166": "兴业银行",
    "sh600000": "浦发银行", "sz002142": "宁波银行", "sh600016": "民生银行",
    "sh601988": "中国银行", "sh601628": "中国人寿", "sh601601": "中国太保",
    "sh600887": "伊利股份", "sh603288": "海天味业", "sz000568": "泸州老窖",
    "sz002304": "洋河股份", "sh600809": "山西汾酒", "sz000596": "古井贡酒",
    "sh600690": "海尔智家", "sz000651": "格力电器", "sz002032": "苏泊尔",
    "sh600104": "上汽集团", "sz000625": "长安汽车", "sh601238": "广汽集团",
    "sz300124": "汇川技术", "sz002475": "立讯精密", "sz000725": "京东方A",
    "sh603501": "韦尔股份", "sz002049": "紫光国微", "sz300782": "卓胜微",
    "sh688012": "中微公司", "sh688008": "澜起科技", "sh688396": "华润微",
    "sh600585": "海螺水泥", "sz000002": "万科A", "sh600048": "保利发展",
    "sh601668": "中国建筑", "sh601390": "中国中铁", "sh601800": "中国交建",
    "sz300274": "阳光电源", "sh688599": "天合光能", "sz002459": "晶澳科技",
    "sh601012": "隆基绿能", "sh600438": "通威股份", "sz300014": "亿纬锂能",
    "sz002230": "科大讯飞", "sh688256": "寒武纪", "sz002415": "海康威视",
    "sz000063": "中兴通讯", "sh600050": "中国联通", "sh600941": "中国移动",
    "sz002129": "中环股份", "sh600009": "上海机场", "sh601111": "中国国航",
    "sh600029": "南方航空", "sz000768": "中航西飞", "sh600893": "航发动力",
    "sh600760": "中航沈飞", "sh600150": "中国船舶", "sh601989": "中国重工",
    "sh601899": "紫金矿业", "sz002460": "赣锋锂业", "sh600547": "山东黄金",
    "sh603799": "华友钴业", "sh600111": "北方稀土", "sz000831": "中国稀土",
    "sz000977": "浪潮信息", "sz000938": "紫光股份", "sh603019": "中科曙光",
    "sh688041": "海光信息", "sh688047": "龙芯中科", "sh688561": "奇安信",
    "sz300033": "同花顺", "sh601066": "中信建投", "sh600837": "海通证券",
}

for code, name in more_a.items():
    if code not in base:
        base[code] = {"n": name, "mkt": "A股", "memo": ""}

# 港股核心
hk = {
    "hk00700": ("腾讯控股","互联网龙头"), "hk09988": ("阿里巴巴","电商龙头"),
    "hk09999": ("网易","游戏"), "hk01810": ("小米集团","手机+汽车"),
    "hk09618": ("京东集团","电商"), "hk09888": ("百度集团","AI"),
    "hk02015": ("理想汽车","新能源车"), "hk09866": ("蔚来","新能源车"),
    "hk01211": ("比亚迪股份","新能源车"), "hk02318": ("中国平安","保险"),
    "hk00388": ("港交所","交易所"), "hk00941": ("中国移动","电信"),
    "hk00883": ("中国海油","能源"), "hk01398": ("工商银行","银行"),
    "hk03988": ("中国银行","银行"), "hk02382": ("舜宇光学","光学"),
    "hk02020": ("安踏体育","运动"), "hk09626": ("哔哩哔哩","视频"),
    "hk01024": ("快手","短视频"), "hk03690": ("美团","本地生活"),
    "hk09698": ("万国数据","数据中心"), "hk09688": ("百度集团","AI"),
    "hk02269": ("药明生物","医药"), "hk01177": ("中国生物制药","医药"),
    "hk00175": ("吉利汽车","汽车"), "hk09868": ("小鹏汽车","新能源车"),
    "hk00005": ("汇丰控股","银行"), "hk01299": ("友邦保险","保险"),
    "hk00016": ("新鸿基地产","地产"), "hk00027": ("银河娱乐","博彩"),
}

for code, (name, memo) in hk.items():
    base[code] = {"n": name, "mkt": "港股", "memo": memo}

# 美股核心
us = {
    "usAAPL": ("苹果 Apple","科技"), "usMSFT": ("微软 Microsoft","AI云"),
    "usGOOGL": ("谷歌 Alphabet","AI搜索"), "usAMZN": ("亚马逊 Amazon","电商云"),
    "usNVDA": ("英伟达 NVIDIA","GPU"), "usMETA": ("Meta","社交"),
    "usTSLA": ("特斯拉 Tesla","电动车"), "usAMD": ("AMD","芯片"),
    "usINTC": ("英特尔 Intel","芯片"), "usNFLX": ("奈飞 Netflix","流媒体"),
    "usBABA": ("阿里巴巴","中概电商"), "usJD": ("京东","中概电商"),
    "usPDD": ("拼多多","电商"), "usBIDU": ("百度","中概AI"),
    "usNIO": ("蔚来","中概电车"), "usXPEV": ("小鹏汽车","中概电车"),
    "usLI": ("理想汽车","中概电车"), "usTCOM": ("携程","中概旅游"),
    "usV": ("Visa","支付"), "usJPM": ("摩根大通","金融"),
    "usJNJ": ("强生","医药"), "usPG": ("宝洁","消费"),
    "usKO": ("可口可乐","消费"), "usWMT": ("沃尔玛","零售"),
    "usDIS": ("迪士尼","娱乐"), "usADBE": ("Adobe","软件"),
    "usCRM": ("Salesforce","SaaS"), "usORCL": ("甲骨文","数据库"),
    "usAVGO": ("博通","芯片"), "usQCOM": ("高通","芯片"),
    "usASML": ("阿斯麦","光刻机"), "usTSM": ("台积电","芯片代工"),
    "usUBER": ("Uber","出行"), "usPLTR": ("Palantir","AI数据分析"),
}

for code, (name, memo) in us.items():
    base[code] = {"n": name, "mkt": "美股", "memo": memo}

print(f"Total stocks: {len(base)} (A-share: {sum(1 for v in base.values() if v['mkt']=='A股')}, HK: {sum(1 for v in base.values() if v['mkt']=='港股')}, US: {sum(1 for v in base.values() if v['mkt']=='美股')})", flush=True)

# 批量拉取A股行情
a_codes = sorted([c for c in base if c.startswith(("sh","sz"))])
print(f"Fetching quotes for {len(a_codes)} A-shares...", flush=True)

def quote(codes):
    try:
        r = subprocess.run([str(NODE), "scripts/index.js", "quote", ",".join(codes)],
            cwd=str(WESTOCK), capture_output=True, text=True, timeout=35, encoding='utf-8', errors='replace')
        if r.returncode: return {}
        result = {}
        for line in r.stdout.strip().split("\n"):
            line = line.strip()
            if not (line.startswith("| sh") or line.startswith("| sz")): continue
            p = [c.strip() for c in line.split("|") if c.strip()]
            if len(p) < 30: continue
            code = p[0]
            try:
                # westock-data columns:
                # 0:code 1:mkt_type 2:mkt_name 3:name 4:symbol 5:price
                # 6:prev_close 7:open 8:high 9:low 10:volume 11:amount
                # 12:change 13:change_pct 14:turnover 15:vol_ratio
                # 16:range_pct 17:avg_price 18:time 19:wb_ratio
                # 20:pe 21:pe_fwd 22:pe_lyr 23:pb 24:div_yield
                # 25:total_mcap 26:circ_mcap 27:total_shares 28:float_shares
                # 29:h52 30:l52 31:chg5d 32:chg10d 33:chg20d 34:chg60d 35:chg_ytd
                result[code] = {
                    "n": p[3], "p": float(p[5] or 0), "chg": float(p[13] or 0),
                    "pe": float(p[20] or 0), "pb": float(p[23] or 0),
                    "dv": float(p[24] or 0), "h52": float(p[29] or 0),
                    "d20": float(p[33] or 0), "d60": float(p[34] or 0),
                    "ytd": float(p[35] or 0)
                }
            except Exception as e:
                pass
        return result
    except Exception as e:
        print(f"  error: {e}", flush=True)
        return {}

for i in range(0, len(a_codes), 15):
    b = a_codes[i:i+15]
    print(f"  {i+1}-{min(i+15, len(a_codes))}/{len(a_codes)}", flush=True)
    qr = quote(b)
    for c, d in qr.items():
        if c in base:
            for k, v in d.items():
                base[c][k] = v
    time.sleep(0.15)

# Ensure all fields exist
for c in base:
    for f in ["p","chg","pe","pb","h52","dv","d20","d60","ytd"]:
        if f not in base[c]: base[c][f] = 0

with open(OUT, "w", encoding="utf-8") as f:
    json.dump(base, f, ensure_ascii=False)
print(f"\nDone! {len(base)} stocks saved", flush=True)
