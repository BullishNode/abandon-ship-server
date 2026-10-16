FROM sha256:ef8279e1ffc6d216da8445fef05dbd1963b11a36f7c1c2aa68dd56ad216700a0
COPY captaind /app/captaind
COPY watchmand /app/watchmand
COPY SOURCE_MANIFEST.json /app/SOURCE_MANIFEST.json
LABEL org.bullishnode.source-key="de9897024bb4b4a534ce359fa1e523104e47207ed6cef022e6b3c2b1299296d7"
LABEL org.bullishnode.server-source-key="0440f31756ee5da2e2ba796d200e0f6a120c9f2f7f16a544e681b497c23d0744"
LABEL org.bullishnode.retained-client-source-key="ba9451d2aac67e0aed930946e9b3a17d64b62c9f497d6e525d46eeded20971c1"
USER 2001:2001
ENV LD_LIBRARY_PATH=/nix/store/k8rbpjlq071djhghgnxm2kcng6h2b035-gcc-15.2.0-libgcc/lib:/nix/store/m07h00fl6538s4gavrp66a20ka4hg7fy-glibc-2.42-67/lib
