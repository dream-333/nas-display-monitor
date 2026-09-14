#!/usr/bin/env python3
"""Versioned, app-only AMOLED upgrade with host deb and standalone Windows flasher."""
from pathlib import Path
from verify_layout import check_links
import hashlib
import json
import shutil
import subprocess
import tempfile
import zipfile
from package_firmware import NAME as FIRST_FLASH_NAME, FACTORY

ROOT=Path(__file__).resolve().parents[1]
from release_config import DIST, ASSETS, HOST_VERSION, TERMINAL_VERSION, FIRMWARE_ID, FIRMWARE_VERSION, RELEASE_NOTES, LOGS, COMPONENTS, USER_DOCS, BUNDLE_NAME, release_metadata
NAME=BUNDLE_NAME+'-Delivery'

def sha(data):return hashlib.sha256(data).hexdigest()

def main():
    DIST.mkdir(parents=True, exist_ok=True)
    build=ASSETS
    for line in (ASSETS/'SHA256SUMS.txt').read_text().splitlines():
        digest, name = line.split('  ', 1)
        assert sha((ASSETS/name).read_bytes()) == digest, name
    fw=(build/'firmware.bin').read_bytes()
    assert FIRMWARE_ID.encode() in fw
    assert (build/'partitions.bin').stat().st_size == 3072, 'Invalid reference partition table'
    assert len(fw)<6553600
    deb=COMPONENTS/f'nas-display-host_{HOST_VERSION}_all.deb'
    assert subprocess.check_output(['dpkg-deb','-f',str(deb),'Version'],text=True).strip()==HOST_VERSION
    tool=(ROOT/'third_party/tools/esptool/esptool-v4.12.0-windows-amd64.zip').read_bytes()
    assert sha(tool)=='42fddc5e6a05716868ad77fb43acbf53be041f97abed87ff850df1dc88140889'
    with tempfile.TemporaryDirectory(prefix='nas-display-release-') as tmp:
        stage=Path(tmp)/NAME;stage.mkdir();win=stage/'Windows-x64';win.mkdir()
        def copy(source,target):
            dest=stage/target;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/source,dest)
        first = COMPONENTS/(FIRST_FLASH_NAME+'.zip')
        with zipfile.ZipFile(first) as z:
            assert z.testzip() is None
            for name in z.namelist():
                assert name.startswith(FIRST_FLASH_NAME+'/')
                rel = Path(name).relative_to(FIRST_FLASH_NAME)
                assert '..' not in rel.parts and not rel.is_absolute()
                dest = stage/'First-Flash'/rel
                dest.parent.mkdir(parents=True,exist_ok=True)
                dest.write_bytes(z.read(name))
        assert (stage/'First-Flash/factory.bin').read_bytes() == (COMPONENTS/FACTORY).read_bytes()
        guide=(ROOT/'docs/delivery/README-FIRST.zh-CN.md').read_text().replace('@HOST_VERSION@',HOST_VERSION).replace('@TERMINAL_VERSION@',TERMINAL_VERSION).replace('@BUNDLE_NAME@',BUNDLE_NAME)
        (stage/'README-FIRST.zh-CN.md').write_text(guide)
        for name in USER_DOCS:
            copy('docs/'+name,'Documentation/'+name)
        (stage/'Debian').mkdir()
        (stage/'Debian/README.zh-CN.md').write_text('# Debian 安装\n\n参阅 [安装说明](../Documentation/INSTALL.zh-CN.md)。\n')
        for name,folder in [(f'NAS-Terminal-fnOS-{TERMINAL_VERSION}-x86.fpk','Terminal'),('AMOLED-Full-Case-V7-Slide-Lock.zip','Mechanical'),('NAS-IT8613-Driver-Kit.zip','Drivers')]:
            copy(COMPONENTS/name,folder+'/'+name)
            copy(COMPONENTS/(name+'.sha256'),folder+'/'+name+'.sha256')
        (stage/'Terminal/README.zh-CN.md').write_text('# NAS Terminal\n\n安装、使用和故障排查见 [终端说明](../Documentation/TERMINAL.zh-CN.md)。\n')
        copy('docs/VALIDATION.zh-CN.md','Terminal/VALIDATION.zh-CN.md')
        copy('THIRD-PARTY-NOTICES.md','Licenses/THIRD-PARTY-NOTICES.md')
        copy(COMPONENTS/f'NAS-Display-fnOS-{HOST_VERSION}-x86.fpk',f'fnOS/NAS-Display-fnOS-{HOST_VERSION}-x86.fpk')
        copy(COMPONENTS/f'NAS-Display-fnOS-{HOST_VERSION}-x86.fpk.sha256',f'fnOS/NAS-Display-fnOS-{HOST_VERSION}-x86.fpk.sha256')
        (stage/'fnOS/README.zh-CN.md').write_text('# fnOS 安装\n\n参阅 [安装说明](../Documentation/INSTALL.zh-CN.md) 和 [驱动准备](../Documentation/DRIVERS.zh-CN.md)。\n')
        copy(ASSETS/'application-icon.png','Preview/application-icon.png')
        copy(ASSETS/'preview.png','Preview/preview.png')
        copy(COMPONENTS/f'nas-display-host_{HOST_VERSION}_all.deb','Debian/'+deb.name)
        copy(COMPONENTS/f'nas-display-host_{HOST_VERSION}_all.deb.sha256','Debian/'+deb.name+'.sha256')
        (win/'firmware.bin').write_bytes(fw)
        with zipfile.ZipFile(ROOT/'third_party/tools/esptool/esptool-v4.12.0-windows-amd64.zip') as z:
            for file in ('esptool.exe','LICENSE'):(win/file).write_bytes(z.read('esptool-windows-amd64/'+file))
        (win/'UPDATE.cmd').write_bytes(b'@echo off\r\npowershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0update.ps1"\r\npause\r\n')
        (win/'update.ps1').write_text(r'''$ErrorActionPreference = 'Stop'
try {
  Set-Location $PSScriptRoot
  $files = Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot 'FILES.json') | ConvertFrom-Json
  foreach ($entry in $files) {
    $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $PSScriptRoot $entry.path)).Hash
    if ($actual -ne $entry.sha256) { throw "Checksum failed: $($entry.path)" }
  }
  Write-Host '@FIRMWARE_VERSION@ update: LILYGO T-Display AMOLED only. Pause NAS USB sending first.'
  Write-Host 'Writes application only; retains pairing and display settings.'
  Write-Host ('Available ports: ' + ([System.IO.Ports.SerialPort]::GetPortNames() -join ', '))
  $port = (Read-Host 'Device port (example COM5)').Trim().ToUpperInvariant()
  if ($port -notmatch '^COM[0-9]+$') { throw 'Invalid COM port' }
  & (Join-Path $PSScriptRoot 'esptool.exe') --chip esp32s3 --port $port --baud 460800 write_flash 0x10000 (Join-Path $PSScriptRoot 'firmware.bin')
  if ($LASTEXITCODE -ne 0) { throw 'Flash failed. Close serial monitor; retry in BOOT mode.' }
  Write-Host 'SUCCESS. Release BOOT; press RESET if needed.' -ForegroundColor Green
} catch { Write-Host $_ -ForegroundColor Red; exit 1 }
'''.replace('@FIRMWARE_VERSION@',FIRMWARE_VERSION),encoding='utf-8-sig')
        for source,target in [(ASSETS/'Licenses/LILYGO-AMOLED.txt','LILYGO-AMOLED.txt'),(ASSETS/'Licenses/TFT_eSPI.txt','TFT_eSPI.txt'),(ASSETS/'Licenses/ArduinoJson.txt','ArduinoJson.txt'),(ASSETS/'Licenses/Dream-UI-Sans.txt','Dream-UI-Sans.txt')]:
            copy(source,'Licenses/'+target)
        for path in (LOGS/f'host-{HOST_VERSION}-tests.log',LOGS/f'host-{HOST_VERSION}-package-check.log'):
            # Logs can contain machine-specific paths: ship a concise verification record instead.
            assert 'OK' in (ROOT/path).read_text() if path.name.endswith('tests.log') else 'PASS deb' in (ROOT/path).read_text()
        assert 'PASS FPK' in (LOGS/f'fpk-{HOST_VERSION}-check.log').read_text()
        assert 'PASS' in (LOGS/f'terminal-{TERMINAL_VERSION}-check.log').read_text()
        copy('docs/VALIDATION.zh-CN.md','VERIFIED.zh-CN.md')
        (win/'FILES.json').write_text(json.dumps([{'path':p.name,'sha256':sha(p.read_bytes())} for p in sorted(win.iterdir()) if p.is_file()],indent=2)+'\n')
        (stage/'RELEASE.json').write_text(json.dumps({'host':HOST_VERSION,'fpk':HOST_VERSION,'terminal':TERMINAL_VERSION,'web_port':8787,'maintainer':'Dream','distributor':'Dream','firmware':FIRMWARE_ID,'offset':'0x10000','firmware_sha256':sha(fw),'first_flash':{'folder':'First-Flash','image':'factory.bin','offset':'0x0','erase_all':True,'sha256':sha((stage/'First-Flash/factory.bin').read_bytes()),'physical_verified':False},'debian_sha256':sha(deb.read_bytes()),'fan_control':'0–100% manual/temperature curves; enabled curves resume on service start; stop restores baseline; physical validation pending','physical_validation_pending':True},indent=2)+'\n')
        for sidecar in stage.rglob('*.sha256'):
            digest,name=sidecar.read_text().strip().split('  ',1)
            assert sha((sidecar.parent/name).read_bytes())==digest,name
        release_file=stage/'RELEASE.json'
        release_data=json.loads(release_file.read_text())
        release_data.update(release_metadata())
        release_file.write_text(json.dumps(release_data,indent=2)+'\n')
        check_links([p for folder in ('Documentation','Debian','fnOS','Terminal','First-Flash') for p in (stage/folder).glob('*.md')], stage)
        entries=[{'path':p.relative_to(stage).as_posix(),'sha256':sha(p.read_bytes())} for p in sorted(stage.rglob('*')) if p.is_file()]
        (stage/'FILES.json').write_text(json.dumps(entries,indent=2)+'\n')
        (stage/'SHA256SUMS.txt').write_text(''.join(sha(p.read_bytes())+'  '+p.relative_to(stage).as_posix()+'\n' for p in sorted(stage.rglob('*')) if p.is_file() and p.name!='SHA256SUMS.txt'))
        subprocess.run(['sha256sum','-c','SHA256SUMS.txt'],cwd=stage,check=True,stdout=subprocess.DEVNULL)
        archive=DIST/(NAME+'.zip')
        with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
            for p in sorted(stage.rglob('*')):
                if p.is_file():z.write(p,NAME+'/'+p.relative_to(stage).as_posix())
        with zipfile.ZipFile(archive) as z:
            assert z.testzip() is None
            for e in entries:assert sha(z.read(NAME+'/'+e['path']))==e['sha256']
            assert not any(Path(n).name in ('auth.json','config.json','fan_service.py') for n in z.namelist())
        Path(str(archive)+'.sha256').write_text(sha(archive.read_bytes())+'  '+archive.name+'\n')
        (COMPONENTS/f'NAS-Display-AMOLED-{FIRMWARE_VERSION}-firmware.bin').write_bytes(fw)
        (COMPONENTS/f'NAS-Display-AMOLED-{FIRMWARE_VERSION}-firmware.bin.sha256').write_text(sha(fw)+f'  NAS-Display-AMOLED-{FIRMWARE_VERSION}-firmware.bin\n')
        print('PASS versioned AMOLED update, established partitions, verified esptool, payload hashes and ZIP CRC:',archive)
        print('ZIP bytes:',archive.stat().st_size)

if __name__=='__main__':main()
