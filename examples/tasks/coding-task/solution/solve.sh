#!/bin/sh
set -eu
cat > /app/solution.py <<'EOF'
def sum_even(values):
    return sum(value for value in values if value % 2 == 0)
EOF
printf '{"implemented": true}\n' > /app/report.json
