CC ?= cc
CFLAGS ?= -O2 -g
WARN = -std=c11 -Wall -Wextra -Werror -Wformat=2 -Wconversion
ANDROID_API ?= 28
NDK_CC = $(ANDROID_NDK_HOME)/toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android$(ANDROID_API)-clang

.PHONY: policy-test ctl-test all android test test-sanitize clean
all: build/a9-eink-compat
build:
	mkdir -p $@
build/a9-eink-compat: eink-compat.c eink-policy.h | build
	$(CC) $(CFLAGS) $(WARN) -pthread $< -o $@
android: | build
	test -x "$(NDK_CC)"
	"$(NDK_CC)" $(CFLAGS) $(WARN) -fPIE -pie -pthread eink-compat.c -o build/a9-eink-compat-android
test: all
	python3 test_compat.py build/a9-eink-compat
build/a9-eink-compat-sanitize: eink-compat.c | build
	$(CC) -O1 -g $(WARN) -pthread -fsanitize=address,undefined -fno-omit-frame-pointer $< -o $@
test-sanitize: build/a9-eink-compat-sanitize
	python3 test_compat.py build/a9-eink-compat-sanitize
clean:
	rm -rf build
build/a9-eink-ctl: a9-eink-ctl.c | build
	$(CC) $(CFLAGS) $(WARN) $< -o $@
ctl-test: build/a9-eink-ctl
	python3 test_ctl.py build/a9-eink-ctl
build/policy-test: policy-test.c eink-policy.h | build
	$(CC) $(CFLAGS) $(WARN) $< -o $@
policy-test: build/policy-test
	build/policy-test
