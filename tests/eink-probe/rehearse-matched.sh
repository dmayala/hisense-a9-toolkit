#!/usr/bin/env bash
# Offline matched-history + HOME-rollback rehearsals. No real phone commands.
set -euo pipefail
D=$(cd "$(dirname "$0")" && pwd)
exec bash "$D/run-tests.sh" test_matched_source test_ghost_matched_core test_ghost_mainline_matched test_ghost_stock_matched test_ghost_matched_analyze test_ghost_recovery test_ghost_tuple test_ghost_preparation test_ghost_attempt test_ghost_home_probe test_ghost_readback
