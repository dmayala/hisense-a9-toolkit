# SPDX-License-Identifier: GPL-2.0-only
# a9tcon.c / a9tcon.h are copied unchanged from scripts/mainline/tcon (A9 repo);
# kcompat/ maps their <stdint.h>/<stddef.h>/<string.h> to kernel headers.
ccflags-y += -I$(src) -I$(src)/kcompat
a9_eink-y := a9_eink_drv.o a9_eink_transport.o a9_eink_guard.o a9_eink_ring.o a9_eink_engine.o a9_eink_damage.o a9tcon.o
obj-$(CONFIG_DRM_A9_EINK) += a9_eink.o
