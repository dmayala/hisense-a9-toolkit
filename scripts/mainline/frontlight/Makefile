# SPDX-License-Identifier: GPL-2.0-only
HOSTCC ?= gcc
BUILD ?= build
CFLAGS ?= -O2 -g

.PHONY: test clean
test: $(BUILD)/test-core
	./$(BUILD)/test-core

$(BUILD)/test-core: tests/test-core.c driver/a9-frontlight-core.c driver/a9-frontlight-core.h
	mkdir -p $(BUILD)
	$(HOSTCC) $(CFLAGS) -std=c11 -Wall -Wextra -Werror -Idriver tests/test-core.c driver/a9-frontlight-core.c -o $@

clean:
	rm -rf $(BUILD)
