# SPDX-License-Identifier: GPL-2.0-only
CC ?= cc
CFLAGS ?= -O2 -Wall -Wextra -Werror
ANDROID_NDK_HOME ?= /nix/store/f2n16g0sj97brhnfvj6wavpgv0qix94b-android-sdk-ndk-27.0.12077973/libexec/android-sdk/ndk/27.0.12077973
ANDROID_CC = $(ANDROID_NDK_HOME)/toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android30-clang
OUT ?= build
.PHONY: all android test
all: $(OUT)/a9-audio-ctl $(OUT)/a9-audio-pcm
$(OUT):
	mkdir -p $@
$(OUT)/a9-audio-ctl: a9-audio-ctl.c | $(OUT)
	$(CC) $(CFLAGS) $< -o $@
$(OUT)/a9-audio-pcm: a9-audio-pcm.c a9-audio-ctl.c a9-audio-quiet.h | $(OUT)
	$(CC) $(CFLAGS) $< -o $@
android: $(OUT)/a9-audio-ctl-android $(OUT)/a9-audio-pcm-android
	cd "$(OUT)" && sha256sum a9-audio-ctl-android a9-audio-pcm-android > SHA256SUMS
$(OUT)/a9-audio-ctl-android: a9-audio-ctl.c | $(OUT)
	$(ANDROID_CC) -O2 -Wall -Wextra -Werror -static $< -o $@
$(OUT)/a9-audio-pcm-android: a9-audio-pcm.c a9-audio-ctl.c a9-audio-quiet.h | $(OUT)
	$(ANDROID_CC) -O2 -Wall -Wextra -Werror -static $< -o $@
test: | $(OUT)
	$(CC) $(CFLAGS) tests/control_test.c -o $(OUT)/control-test
	$(OUT)/control-test
	$(CC) $(CFLAGS) tests/pcm_test.c -o $(OUT)/pcm-test
	$(OUT)/pcm-test
