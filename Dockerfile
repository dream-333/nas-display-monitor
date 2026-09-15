FROM docker.io/library/python:3.12-slim-bookworm@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254 AS driver-build
RUN rm /etc/apt/sources.list.d/debian.sources && printf 'deb [check-valid-until=no] https://snapshot.debian.org/archive/debian/20250908T000000Z/ bookworm main\n' > /etc/apt/sources.list
RUN apt-get -o Acquire::Retries=3 update && apt-get install -y --no-install-recommends gcc-12 make binutils kmod linux-headers-6.1.0-39-amd64=6.1.148-1 linux-image-6.1.0-39-amd64=6.1.148-1 && rm -rf /var/lib/apt/lists/*
COPY hardware/drivers/it87/source/ /driver-source/
COPY tools/build_it8613_module.py /build_it8613_module.py
RUN python /build_it8613_module.py --source /driver-source --output /opt/it8613
# Include the matching kernel source for the redistributed stock hwmon-vid module.
RUN apt-get -o Acquire::Retries=3 update && cd /tmp && apt-get download linux-source-6.1=6.1.148-1 \
    && dpkg-deb -x linux-source-6.1_6.1.148-1_all.deb /tmp/kernel-source \
    && cp /tmp/kernel-source/usr/src/linux-source-6.1.tar.xz /opt/it8613/ \
    && cp /boot/config-6.1.0-39-amd64 /opt/it8613/kernel.config \
    && rm -rf /tmp/kernel-source /tmp/linux-source-6.1_6.1.148-1_all.deb /var/lib/apt/lists/*

# Existing locked wheels target CPython 3.12 / Linux x86_64.
FROM docker.io/library/python:3.12-slim-bookworm@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254
LABEL org.opencontainers.image.title="NAS Display" \
      org.opencontainers.image.version="1.5.2" \
      org.opencontainers.image.source="https://github.com/dream-333/nas-display-monitor"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    NAS_DISPLAY_STATE_DIR=/data NAS_DISPLAY_PORT=8787
WORKDIR /app
COPY third_party/tools/fnos/wheels/ /tmp/wheels/
COPY src/host/fnos/vendor-lock.json /tmp/vendor-lock.json
RUN python -c 'import hashlib,json,pathlib; p=pathlib.Path("/tmp"); lock=json.loads((p/"vendor-lock.json").read_text()); files=list((p/"wheels").glob("*.whl")); assert files; assert all(hashlib.sha256(f.read_bytes()).hexdigest()==lock["wheels/"+f.name] for f in files), "Wheel checksum mismatch"' \
    && pip install --no-cache-dir --no-index --find-links=/tmp/wheels Flask==3.1.3 waitress==3.0.2 pyserial==3.5 \
    && rm -rf /tmp/wheels \
    && groupadd --gid 10001 nas-display \
    && useradd --uid 10001 --gid nas-display --no-create-home nas-display \
    && mkdir /data /fan-state && chown nas-display:nas-display /data
COPY third_party/tools/fnos/smartmontools_7.3-1+b1_amd64.deb third_party/tools/fnos/smartmontools_7.3.orig.tar.xz third_party/tools/fnos/smartmontools_7.3-1.debian.tar.xz third_party/tools/fnos/smartmontools_7.3-1.dsc /tmp/smart-inputs/
RUN python -c 'import hashlib,json,pathlib; p=pathlib.Path("/tmp"); lock=json.loads((p/"vendor-lock.json").read_text()); assert all(hashlib.sha256(f.read_bytes()).hexdigest()==lock[f.name] for f in (p/"smart-inputs").iterdir()), "SMART input checksum mismatch"' \
    && dpkg-deb -x /tmp/smart-inputs/smartmontools_7.3-1+b1_amd64.deb /tmp/smart-package \
    && cp /tmp/smart-package/usr/sbin/smartctl /usr/sbin/smartctl \
    && mkdir -p /usr/share/doc/nas-display/smartmontools \
    && cp /tmp/smart-package/usr/share/doc/smartmontools/copyright /usr/share/doc/nas-display/smartmontools/ \
    && cp /tmp/smart-inputs/*.tar.xz /tmp/smart-inputs/*.dsc /usr/share/doc/nas-display/smartmontools/ \
    && smartctl --version >/dev/null \
    && rm -rf /tmp/smart-inputs /tmp/smart-package /tmp/vendor-lock.json
COPY src/host/*.py /app/
COPY src/host/templates/ /app/templates/
COPY src/host/static/ /app/static/
COPY src/collector/*.py /app/
COPY src/host/fnos/fan_control.py /app/fpk_fan.py
COPY src/host/docker/ /app/docker/
COPY --from=driver-build /opt/it8613/ /opt/it8613/
LABEL org.nas-display.driver.kernel="6.1.0-39-amd64"
COPY THIRD-PARTY-NOTICES.md /app/THIRD-PARTY-NOTICES.md
USER 10001:10001
EXPOSE 8787
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 CMD ["python", "/app/docker/healthcheck.py"]
ENTRYPOINT ["python", "/app/docker/entrypoint.py"]
