# Host side of the a9_eink driver: the waveform extractor and the engine test
# harness. Same engine source as the kernel module.
#   nix-shell -p gcc gnumake --run 'make -C scripts/mainline/eink'
# Output in $(O) (default ./build, gitignored).
O ?= build
CC ?= cc
TCON = ../tcon
CFLAGS ?= -O2 -g -std=c11 -Wall -Wextra -Wshadow
CPPFLAGS += -Idriver -I$(TCON)
CORE = $(TCON)/a9tcon.c $(TCON)/a9tcon-load.c driver/a9_eink_engine.c driver/a9_eink_damage.c
DEPS = $(CORE) driver/a9_eink_engine.h $(TCON)/a9tcon.h

all: $(O)/a9wf-extract $(O)/a9e-test $(O)/convert-equiv $(O)/damage-equiv $(O)/pp-model $(O)/same-equiv

$(O):
	mkdir -p $(O)

$(O)/a9wf-extract: tools/a9wf-extract.c $(DEPS) | $(O)
	$(CC) $(CPPFLAGS) $(CFLAGS) -o $@ tools/a9wf-extract.c $(CORE)

$(O)/a9e-test: test/a9e-test.c $(DEPS) | $(O)
	$(CC) $(CPPFLAGS) $(CFLAGS) -o $@ test/a9e-test.c $(CORE)

$(O)/convert-equiv: test/convert-equiv.c $(DEPS) | $(O)
	$(CC) $(CPPFLAGS) $(CFLAGS) -o $@ test/convert-equiv.c $(CORE)

$(O)/same-equiv: test/same-equiv.c $(DEPS) | $(O)
	$(CC) $(CPPFLAGS) $(CFLAGS) -o $@ test/same-equiv.c $(CORE)

$(O)/damage-equiv: test/damage-equiv.c $(DEPS) | $(O)
	$(CC) $(CPPFLAGS) $(CFLAGS) -o $@ test/damage-equiv.c $(CORE)

$(O)/pp-model: test/pp-model.c $(DEPS) | $(O)
	$(CC) $(CPPFLAGS) $(CFLAGS) -o $@ test/pp-model.c $(CORE)

# static aarch64 extractor, to run on the phone itself (optional)
$(O)/a9wf-extract.aarch64: tools/a9wf-extract.c $(DEPS) | $(O)
	aarch64-unknown-linux-gnu-gcc -static $(CPPFLAGS) $(CFLAGS) -o $@ tools/a9wf-extract.c $(CORE)

clean:
	rm -rf $(O)

.PHONY: all clean
