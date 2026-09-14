"""Render a contact sheet from the actual C++ drawing harness, with identical embedded glyph pixels."""
from pathlib import Path
import re
import subprocess
import tempfile
import cairosvg
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'.build/previews'; OUT.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory(prefix='nas-ui5-preview-') as tmp:
    executable=str(Path(tmp)/'preview')
    subprocess.run(['g++','-std=c++17','-I'+str(ROOT/'src/firmware/include'),str(ROOT/'tools/preview_ui.cpp'),'-o',executable],check=True)
    subprocess.run([executable,str(OUT/'NAS-UI5.1-preview.html')],check=True)
s=(OUT/'NAS-UI5.1-preview.html').read_text()
svgs=re.findall(r"<svg viewBox='0 0 536 240'>(.*?)</svg>",s)
assert len(svgs)>=12
parts=['<svg xmlns="http://www.w3.org/2000/svg" width="1120" height="584" viewBox="0 0 1120 584"><rect width="1120" height="584" fill="#0b1014"/>']
for i,body in enumerate(svgs[:4]):
    x=16+(i%2)*552;y=40+(i//2)*280
    parts.append(f'<g transform="translate({x},{y})"><rect width="536" height="240" rx="12" fill="black"/>{body}</g>')
parts.append('</svg>')
svg=''.join(parts);(OUT/'NAS-UI5.1-preview.svg').write_text(svg)
cairosvg.svg2png(bytestring=svg.encode(),write_to=str(OUT/'NAS-UI5.1-preview.png'))
print('PASS production drawing + button assertions; preview renders with identical embedded glyph pixels')

for index,name in [(0,'overview'),(1,'thermal'),(3,'storage'),(len(svgs)-1,'settings')]:
    native='<svg xmlns="http://www.w3.org/2000/svg" width="536" height="240"><rect width="536" height="240" fill="black"/>'+svgs[index]+'</svg>'
    cairosvg.svg2png(bytestring=native.encode(),write_to=str(OUT/f'NAS-UI5.1-{name}.png'))
