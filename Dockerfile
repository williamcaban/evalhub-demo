# Custom EvalHub Inspect provider image.
#
# The upstream image currently contains inspect-ai 0.3.251. Keep the runtime
# and EvalHub adapter from that image, but pin the framework to the version
# that supports per-model endpoint overrides.
FROM quay.io/evalhub/community-inspect:latest

RUN python -m pip install --no-cache-dir --upgrade "inspect-ai==0.3.263" \
    && python -c "from importlib.metadata import version; print(version('inspect-ai'))"
