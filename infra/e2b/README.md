# Self-hosted sandbox boundary

E2B is deployed independently on Linux/KVM; it is not emulated by this Compose
stack. Pin and validate a runtime/SDK combination in S0. Check API and sandbox
routing, authentication, template builds, internal inference reachability and
cleanup. Never commit private hostnames, keys or production configuration.
