# Fork images from release binaries built natively (Ubuntu 24.04 glibc) at
# FORK_COMMIT, CARGO_PROFILE_RELEASE_LTO=false. Context: a directory holding
# captaind, watchmand, bark and barkd.
#   docker build -f images.Dockerfile --target captaind -t abandon-ship/captaind:fallback-<commit> <bins>
#   docker build -f images.Dockerfile --target bark     -t abandon-ship/bark:fallback-<commit> <bins>
FROM ubuntu:24.04 AS base
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates libssl3t64 libstdc++6 \
	&& rm -rf /var/lib/apt/lists/*
ARG FORK_COMMIT
LABEL org.opencontainers.image.source=https://github.com/BullishNode/abandon-ship-bark \
	org.opencontainers.image.revision=${FORK_COMMIT}

FROM base AS captaind
COPY captaind watchmand /usr/local/bin/
# Fresh volumes copy these directories' ownership from the image.
RUN mkdir -p /data/captaind /data/watchmand /receipts && chown -R 1000:1000 /data /receipts
USER 1000:1000
ENTRYPOINT ["captaind"]

FROM base AS bark
COPY bark barkd /usr/local/bin/
RUN mkdir -p /data && chown 1000:1000 /data
USER 1000:1000
CMD ["barkd"]
