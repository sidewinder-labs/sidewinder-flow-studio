# ── Flow Studio Backend ───────────────────────────────────────────────────────
# Python 3.13 slim — production-ready image
FROM python:3.13-slim

# System deps needed by some SQLAlchemy drivers + matplotlib
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy the full application, then install it as a regular (non-editable)
# package. A regular install reliably resolves and installs every dependency
# declared in pyproject.toml; editable (-e) installs are for local dev and can
# behave inconsistently inside containers.
# Connector drivers and the pushdown engines are optional extras in pyproject.toml.
# Override with --build-arg SFS_EXTRAS=trino,pushdown (or SFS_EXTRAS= for core only).
# The build context is the repository root (so plugins/ is visible):
#   docker build -f backend/Dockerfile .
# Versions come from locks/requirements.lock (a pip constraints file), so the
# same commit builds the same image on any day; pyproject.toml only states ranges.
ARG SFS_EXTRAS=all-connectors,pushdown
COPY locks/requirements.lock /opt/sfs-lock/requirements.lock
COPY scripts/check_lock.py /opt/sfs-lock/check_lock.py
COPY backend/ .
RUN pip install --no-cache-dir -c /opt/sfs-lock/requirements.lock ".${SFS_EXTRAS:+[$SFS_EXTRAS]}"

# Plugins from plugins/, by short name (sfs-plugin-<name>), comma-separated.
# Override with --build-arg SFS_PLUGINS=curvefit,sklearn (or SFS_PLUGINS= for none).
ARG SFS_PLUGINS=curvefit,tm1,powerbi
COPY plugins/ /opt/sfs-plugins/
RUN for p in $(echo "$SFS_PLUGINS" | tr ',' ' '); do \
      pip install --no-cache-dir -c /opt/sfs-lock/requirements.lock "/opt/sfs-plugins/sfs-plugin-$p" || exit 1; \
    done

# httpx is sometimes skipped by pip's resolver as "already satisfied" in slim
# base images even though it's not actually importable. Force it explicitly so
# the module is guaranteed present.
RUN pip install --no-cache-dir --force-reinstall --no-deps -c /opt/sfs-lock/requirements.lock httpx \
    && python -c "import httpx; print('httpx', httpx.__version__, 'OK')"

# Fail the build if anything installed differs from the lock.
RUN python /opt/sfs-lock/check_lock.py /opt/sfs-lock/requirements.lock

# What this image provides, so plugin packs never ship a second copy of it
RUN python -m app.packs.tools bundled --out /opt/sfs-lock/bundled-distributions.json

# One-line summary of installed connector drivers / pushdown engines.
RUN python scripts/driver_summary.py

# Create data directories
RUN mkdir -p /data/projects /data/frame_cache /data/reports_data

# Non-root user for security
RUN useradd -m -u 1000 flowstudio && chown -R flowstudio:flowstudio /app /data
USER flowstudio

EXPOSE 8000

# Environment overrides — set these in docker-compose or at runtime
ENV HOST=0.0.0.0 \
    PORT=8000 \
    SFS_INSTALL=docker \
    SFS_BUNDLED_DISTRIBUTIONS=/opt/sfs-lock/bundled-distributions.json \
    DEBUG=false \
    PROJECTS_DIR=/data/projects \
    FRAME_CACHE_DIR=/data/frame_cache \
    METADATA_DB_URL=sqlite+aiosqlite:////data/flow_studio.db

CMD ["python", "main.py"]
