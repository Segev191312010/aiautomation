
import base64, pathlib
data = open("payload.b64").read().strip()
src = base64.b64decode(data).decode("utf-8")
pathlib.Path("rule_templates.py").write_text(src, encoding="utf-8")
print("Written rule_templates.py")
