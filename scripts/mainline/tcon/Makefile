# a9tcon: C port of scripts/mainline/a9tcon.py. Build with the system or a nix-shell gcc:
#   nix-shell -p gcc gnumake --run 'make -C scripts/mainline/tcon'
# Output goes to $(O) (default ./build, gitignored).
O ?= build
CC ?= cc
CFLAGS ?= -O2 -g -std=c11 -Wall -Wextra -Wpedantic -Wshadow -Wconversion -Wno-sign-conversion
CORE = a9tcon.c a9tcon-load.c

all: $(O)/a9tcon $(O)/liba9tcon.so

$(O):
	mkdir -p $(O)

$(O)/a9tcon: $(CORE) a9tcon-cli.c a9tcon.h a9t-template-file.h | $(O)
	$(CC) $(CFLAGS) -o $@ $(CORE) a9tcon-cli.c

$(O)/liba9tcon.so: $(CORE) a9tcon.h | $(O)
	$(CC) $(CFLAGS) -fPIC -shared -o $@ $(CORE)

# the core alone, freestanding: proves it needs nothing beyond memcpy/memset
freestanding: a9tcon.c a9tcon.h | $(O)
	$(CC) -std=c11 -O2 -Wall -Wextra -ffreestanding -fno-builtin -nostdlib -c -o $(O)/a9tcon-core.o a9tcon.c
	@nm -u $(O)/a9tcon-core.o

clean:
	rm -rf $(O)

.PHONY: all clean freestanding
