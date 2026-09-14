#!/usr/bin/env python3
"""Build a standalone first-install bundle from fixed, verified UI5.1 inputs."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile
from firmware_images import SEGMENTS, FLASH_SIZE, compose
from release_config import ROOT, ASSETS, COMPONENTS, FIRMWARE_VERSION, DELIVERY_REVISION, release_metadata

NAME = f'NAS-Display-AMOLED-{FIRMWARE_VERSION}-First-Flash-r{DELIVERY_REVISION}'
FACTORY = f'NAS-Display-AMOLED-{FIRMWARE_VERSION}-factory.bin'
TOOL_SHA = '42fddc5e6a05716868ad77fb43acbf53be041f97abed87ff850df1dc88140889'


def sha(data): return hashlib.sha256(data).hexdigest()


def build(stage):
    for line in (ASSETS/'SHA256SUMS.txt').read_text().splitlines():
        digest, name = line.split('  ', 1)
        if sha((ASSETS/name).read_bytes()) != digest: raise ValueError('Fixed input changed: '+name)
    merged, parts = compose(ASSETS)
    stage.mkdir(parents=True)
    (stage/'factory.bin').write_bytes(merged)
    for name, raw in parts.items(): (stage/name).write_bytes(raw)
    for name in ('BOOT-SOURCES.json', 'partitions.csv'):
        shutil.copyfile(ASSETS/name, stage/name)
    shutil.copytree(ASSETS/'Licenses', stage/'Licenses')
    for name in ('first_flash.py', 'first-flash.ps1'):
        shutil.copyfile(ROOT/'tools/flash'/name, stage/name)
    (stage/'FIRST-FLASH.cmd').write_bytes(b'@echo off\r\npowershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0first-flash.ps1"\r\nset "FLASH_RESULT=%ERRORLEVEL%"\r\npause\r\nexit /b %FLASH_RESULT%\r\n')
    shutil.copyfile(ROOT/'docs/FIRST-FLASH.zh-CN.md', stage/'README.zh-CN.md')
    tool = ROOT/'third_party/tools/esptool/esptool-v4.12.0-windows-amd64.zip'
    if sha(tool.read_bytes()) != TOOL_SHA: raise ValueError('Windows esptool changed')
    with zipfile.ZipFile(tool) as z:
        for name, dest in [('esptool.exe', 'esptool.exe'), ('LICENSE', 'Licenses/esptool.txt')]:
            (stage/dest).write_bytes(z.read('esptool-windows-amd64/'+name))
    metadata = dict(release_metadata(), chip='esp32s3', board='LILYGO T-Display-S3 AMOLED non-touch 1.91 inch',
        flash_size=FLASH_SIZE, offset='0x0', erase_all=True, file='factory.bin', sha256=sha(merged),
        physical_first_flash_verified=False,
        segments=[dict(offset=hex(offset), file=name, bytes=len(parts[name]), sha256=sha(parts[name])) for offset,name in SEGMENTS])
    (stage/'FLASH.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2)+'\n')
    entries = [{'path':p.relative_to(stage).as_posix(),'sha256':sha(p.read_bytes())} for p in sorted(stage.rglob('*')) if p.is_file()]
    (stage/'FILES.json').write_text(json.dumps(entries,indent=2)+'\n')
    (stage/'SHA256SUMS.txt').write_text(''.join(sha(p.read_bytes())+'  '+p.relative_to(stage).as_posix()+'\n'
        for p in sorted(stage.rglob('*')) if p.is_file() and p.name != 'SHA256SUMS.txt'))
    return merged


def main():
    COMPONENTS.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='nas-first-flash-') as temp:
        stage = Path(temp)/NAME
        merged = build(stage)
        subprocess.run([sys.executable,str(stage/'first_flash.py'),'--port','/dev/ttyACM0','--dry-run'],check=True,stdout=subprocess.DEVNULL)
        subprocess.run(['sha256sum','-c','SHA256SUMS.txt'],cwd=stage,check=True,stdout=subprocess.DEVNULL)
        out = COMPONENTS/(NAME+'.zip')
        with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
            for p in sorted(stage.rglob('*')):
                if p.is_file():z.write(p,NAME+'/'+p.relative_to(stage).as_posix())
        with zipfile.ZipFile(out) as z:
            assert z.testzip() is None
            for p in stage.rglob('*'):
                if p.is_file(): assert z.read(NAME+'/'+p.relative_to(stage).as_posix())==p.read_bytes()
        (COMPONENTS/FACTORY).write_bytes(merged)
        for p in (out,COMPONENTS/FACTORY):p.with_name(p.name+'.sha256').write_text(sha(p.read_bytes())+'  '+p.name+'\n')
    print('PASS ESP image checksums, partition MD5/layout, merged segments, flasher dry-run and standalone ZIP:',out)


if __name__ == '__main__': main()
