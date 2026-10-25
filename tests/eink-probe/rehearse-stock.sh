#!/usr/bin/env bash
# Stock supervisor admission/success/lock/rerun + real session swipe dismissal.
# All commands are private fake transports; only a localhost camera is used.
set -euo pipefail
D=$(cd "$(dirname "$0")" && pwd)
exec bash "$D/run-tests.sh" test_ghost_stock_runner test_ghost_ab.StockSession.test_swipe_keyguard_is_dismissed_and_the_run_proceeds
