# The only way out of the sandbox network: tinyproxy that lets through only the hosts in the allowlist.
# shared/sandbox/setup.sh writes allowlist.generated from the settings before it builds this image.
FROM alpine:3.20
RUN apk add --no-cache tinyproxy
COPY tinyproxy.conf /etc/tinyproxy/tinyproxy.conf
COPY allowlist.generated /etc/tinyproxy/allowlist
EXPOSE 8888
USER tinyproxy
ENTRYPOINT ["tinyproxy", "-d", "-c", "/etc/tinyproxy/tinyproxy.conf"]
