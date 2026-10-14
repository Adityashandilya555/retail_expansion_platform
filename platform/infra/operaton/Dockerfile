# Z-Matrix Operaton engine (S1-02, #11): the official image, REST only, configured by default.yml. No engine code changes.
# Digest = the multi-arch index of operaton/operaton:2.1.5 (linux/amd64 + linux/arm64), not a single-platform manifest.
FROM operaton/operaton:2.1.5@sha256:ed5d68637e2024dee93c3de19472d3ba4e835073c7c8d66f2bd3f36c15616892
COPY default.yml /operaton/configuration/default.yml
# Already the base image's user; stated so readers and scanners don't have to know that.
USER operaton
# operaton.sh keeps tini, maps DB_* to SPRING_DATASOURCE_*, waits for WAIT_FOR, then passes --rest to run.sh (no webapps).
CMD ["./operaton.sh", "--rest"]
