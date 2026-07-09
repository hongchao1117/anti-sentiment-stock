"""Full search validation"""
import json
db = json.load(open(r"C:\Users\Administrator\Desktop\project\anti-sentiment-stock\stocks_db.json", encoding="utf-8"))

P = {
    'zhong':'中','guo':'国','xin':'信芯新','dian':'电点','zi':'子资','ke':'科',
    'ji':'集机技','wang':'网旺','yin':'银','hang':'行','yao':'药','qi':'汽企器','che':'车',
    'neng':'能','yuan':'源远','guang':'光广','fu':'伏富福复','feng':'风丰峰','hai':'海',
    'you':'油有又','teng':'腾','xun':'讯','jing':'京经精','dong':'东动','fang':'方房',
    'mei':'美每煤','tuan':'团','ping':'平','an':'安','quan':'全','yun':'云运',
    'shu':'数术','ju':'据聚巨','li':'力利立','wu':'物五务','sheng':'生升盛',
    'bi':'比笔','ya':'亚压雅','di':'迪地第','te':'特','si':'斯思司','la':'拉',
    'jie':'杰接解','han':'韩汉','ban':'半办板','dao':'导道到倒','ti':'体提题',
    'mao':'茅毛','tai':'台太泰','yang':'羊阳杨洋','bai':'百白','jiu':'酒久',
    'gong':'工公功','zheng':'证正','bao':'保宝','xian':'先险','hua':'华','da':'大达',
    'tx':'腾讯','al':'阿里巴巴','bd':'半导体','bdt':'半导体','jd':'京东','mt':'美团',
    'byd':'比亚迪','wly':'五粮液','zg':'中国','zx':'中信','zs':'招商','yl':'医疗',
    'dc':'电池','gf':'光伏','bx':'保险','zq':'证券','kj':'科技','ny':'能源',
    'qc':'汽车','rj':'软件','sh':'上海','sz':'深圳','bj':'北京','cn':'储能',
}

all_ok = 0
all_fail = 0
for q in ['zhong','bd','bdt','maotai','wuliang','tx','al','半导','腾讯','600519','300502','zhonghan','茅台','中韩']:
    ql = q.lower().replace(' ','')
    has_cn = any('\u4e00'<=c<='\u9fff' for c in q)
    ms = []
    for code,s in db.items():
        sc = code.replace('sh','').replace('sz','').replace('hk','').replace('us','')
        hay = (s['n']+'|'+code+'|'+sc+'|'+sc.lstrip('0')+'|'+s.get('memo','')).lower()
        m = ql in hay
        if not m and not has_cn and len(ql)>=2:
            for k,chs in P.items():
                if k in ql:
                    for ch in chs:
                        if ch in s['n']: m=True; break
                if m: break
        if m: ms.append(s['n'])
    ok = len(ms) > 0
    if ok: all_ok += 1
    else: all_fail += 1
    label = "OK" if ok else "FAIL"
    print(f"  {q:12s} -> {label:4s} ({len(ms):2d}) {', '.join(ms[:3])}")

print(f"\n{all_ok}/{all_ok+all_fail} passed")
