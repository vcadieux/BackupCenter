"""Generate the browser catalog from messages.json (no runtime dependency)."""
import json
from pathlib import Path
root=Path(__file__).resolve().parents[1]
messages=json.loads((root/'messages.json').read_text(encoding='utf-8'))
(root/'public/messages.js').write_text('window.BC_MESSAGES='+json.dumps(messages,ensure_ascii=False)+';\n',encoding='utf-8')
