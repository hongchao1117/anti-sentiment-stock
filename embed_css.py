"""Extract CSS from HTML and embed into stock_server.py as STYLE constant"""
import re

html = open(r"C:\Users\Administrator\Desktop\project\anti-sentiment-stock\anti-retail-sentiment.html", encoding="utf-8").read()
css = html.split("<style>")[1].split("</style>")[0].strip()

server = open(r"C:\Users\Administrator\Desktop\project\anti-sentiment-stock\stock_server.py", encoding="utf-8").read()

# Build STYLE constant
style_lines = []
for line in css.split("\n"):
    escaped = line.replace("\\", "\\\\").replace('"', '\\"')
    style_lines.append(f'        "{escaped}"')

style_var = "STYLE = (\n" + "\n".join(style_lines) + "\n)"
server = re.sub(r"STYLE = \([\s\S]*?\)", style_var, server, count=1)

open(r"C:\Users\Administrator\Desktop\project\anti-sentiment-stock\stock_server.py", "w", encoding="utf-8").write(server)
print(f"CSS embedded: {len(css)} chars -> {len(style_var)} chars")
